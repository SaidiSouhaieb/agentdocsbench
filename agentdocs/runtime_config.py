"""Load the optional Docker runtime configuration.

The benchmark file describes the task. This file describes where that task
executes. Runtime settings are not part of the benchmark fingerprint.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agentdocs.execution.errors import RuntimeConfigError

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_TARGETS = frozenset(
    {
        "/",
        "/workspace",
        "/workspace/project",
        "/workspace/docs",
        "/workspace/verifier",
        "/var/run/docker.sock",
    }
)


class MountConfig(BaseModel):
    """One explicit agent-container bind mount."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    read_only: bool = True


class AgentRuntimeConfig(BaseModel):
    """Docker settings for one coding-agent CLI."""

    model_config = ConfigDict(extra="forbid")

    image: str
    network: Literal["bridge", "none"] = "bridge"
    env_passthrough: list[str] = Field(default_factory=list)
    mounts: list[MountConfig] = Field(default_factory=list)

    @field_validator("image")
    @classmethod
    def image_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("image must not be blank")
        return stripped

    @field_validator("env_passthrough")
    @classmethod
    def env_names_must_be_identifiers(cls, value: list[str]) -> list[str]:
        for name in value:
            if _ENV_NAME.fullmatch(name) is None:
                raise ValueError(f"invalid environment variable name: {name}")
        return value


class VerifierRuntimeConfig(BaseModel):
    """Docker image that already contains the verifier command."""

    model_config = ConfigDict(extra="forbid")

    image: str

    @field_validator("image")
    @classmethod
    def image_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("image must not be blank")
        return stripped


class AgentsRuntimeConfig(BaseModel):
    """Agent images keyed by AgentDocsBench agent type.

    Only the agent types used by a run need an entry.
    """

    model_config = ConfigDict(extra="forbid")

    codex: AgentRuntimeConfig | None = None
    claude: AgentRuntimeConfig | None = None
    cursor: AgentRuntimeConfig | None = None

    def for_agent(self, agent_type: str) -> AgentRuntimeConfig:
        """Return the entry for ``agent_type`` or raise ``RuntimeConfigError``."""
        selected = getattr(self, agent_type, None)
        if isinstance(selected, AgentRuntimeConfig):
            return selected
        raise RuntimeConfigError(
            f"Runtime config has no agents.{agent_type} entry. "
            "Add an image for each agent type this run uses. "
            "AgentDocsBench does not install provider CLIs."
        )


class RuntimeConfig(BaseModel):
    """Schema version 1. The only backend is ``docker``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    backend: Literal["docker"]
    agents: AgentsRuntimeConfig = Field(default_factory=AgentsRuntimeConfig)
    verifier: VerifierRuntimeConfig


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    """Load a runtime config file.

    Relative mount sources are resolved against the config file's directory.
    ``~`` is expanded. Resolved host paths stay in memory for the mount and
    are not written to run artifacts.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Runtime config file not found: {config_path}")
    if not config_path.is_file():
        raise RuntimeConfigError(f"Runtime config path is not a file: {config_path}")
    data = _read_mapping(config_path)
    try:
        config = RuntimeConfig.model_validate(data)
    except ValidationError as exc:
        raise RuntimeConfigError(_format_validation(config_path, exc)) from exc
    return _resolve_mounts(config, config_path.resolve().parent)


def require_env_passthrough(names: list[str], *, agent_type: str) -> dict[str, str]:
    """Read named host environment variables for the agent container.

    A listed name that is unset raises ``RuntimeConfigError`` before the
    provider process is started. Values are returned to the caller and are
    not persisted.
    """
    values: dict[str, str] = {}
    for name in names:
        if name not in os.environ:
            raise RuntimeConfigError(
                f"Environment variable {name} is listed in "
                f"agents.{agent_type}.env_passthrough but is not set."
            )
        values[name] = os.environ[name]
    return values


def _read_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise RuntimeConfigError(f"Runtime config file is empty: {path}")
    if not isinstance(data, dict):
        raise RuntimeConfigError(
            f"Runtime config must contain a YAML mapping, not {type(data).__name__}: {path}"
        )
    return data


def _format_validation(path: Path, exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "runtime config"
        parts.append(f"{location}: {error['msg']}")
    detail = "; ".join(parts)
    return f"Invalid runtime config {path}: {detail}"


def _resolve_mounts(config: RuntimeConfig, config_dir: Path) -> RuntimeConfig:
    agents = config.agents
    return config.model_copy(
        update={
            "agents": agents.model_copy(
                update={
                    "codex": _resolve_agent_mounts(agents.codex, config_dir, "codex"),
                    "claude": _resolve_agent_mounts(agents.claude, config_dir, "claude"),
                    "cursor": _resolve_agent_mounts(agents.cursor, config_dir, "cursor"),
                }
            )
        }
    )


def _resolve_agent_mounts(
    agent: AgentRuntimeConfig | None,
    config_dir: Path,
    agent_type: str,
) -> AgentRuntimeConfig | None:
    if agent is None:
        return None
    seen: set[str] = set()
    mounts: list[MountConfig] = []
    for mount in agent.mounts:
        target = _validate_target(mount.target)
        if target in seen:
            raise RuntimeConfigError(
                f"agents.{agent_type} mounts the container path {target} more than once."
            )
        seen.add(target)
        source = _resolve_source(mount.source, config_dir)
        mounts.append(mount.model_copy(update={"source": str(source), "target": target}))
    return agent.model_copy(update={"mounts": mounts})


def _validate_target(target: str) -> str:
    if not target.startswith("/"):
        raise RuntimeConfigError(
            f"Mount target must be an absolute container path: {target}"
        )
    pure = PurePosixPath(target)
    if ".." in pure.parts:
        raise RuntimeConfigError(f"Mount target must not contain '..': {target}")
    normalized = pure.as_posix()
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if normalized == "/var/run/docker.sock" or normalized.endswith("/docker.sock"):
        raise RuntimeConfigError(
            "Mounting the Docker socket is not allowed. "
            f"Rejected target: {target}"
        )
    if normalized in _RESERVED_TARGETS or normalized.startswith("/workspace/"):
        raise RuntimeConfigError(f"Mount target is reserved: {target}")
    return normalized


def _resolve_source(source: str, config_dir: Path) -> Path:
    if not source.strip():
        raise RuntimeConfigError("Mount source must not be blank.")
    expanded = Path(source).expanduser()
    resolved = expanded.resolve() if expanded.is_absolute() else (config_dir / expanded).resolve()
    if resolved.as_posix() == "/":
        raise RuntimeConfigError(
            "Mounting the filesystem root is not allowed. Rejected source: /"
        )
    if resolved.name == "docker.sock" or resolved.as_posix().endswith("/docker.sock"):
        raise RuntimeConfigError(
            "Mounting the Docker socket is not allowed. "
            f"Rejected source: {source}"
        )
    if not resolved.exists():
        raise RuntimeConfigError(f"Mount source does not exist: {source}")
    _require_file_or_directory(resolved, source)
    return resolved


def _require_file_or_directory(path: Path, configured: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise RuntimeConfigError(f"Mount source could not be read: {configured}") from exc
    if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        raise RuntimeConfigError(
            f"Mount source must be a regular file or directory: {configured}"
        )
    followed = path.stat()
    if stat.S_ISSOCK(followed.st_mode) or stat.S_ISFIFO(followed.st_mode):
        raise RuntimeConfigError(
            f"Mount source must be a regular file or directory: {configured}"
        )
    if stat.S_ISCHR(followed.st_mode) or stat.S_ISBLK(followed.st_mode):
        raise RuntimeConfigError(
            f"Mount source must be a regular file or directory: {configured}"
        )
    if not path.is_file() and not path.is_dir():
        raise RuntimeConfigError(
            f"Mount source must be a regular file or directory: {configured}"
        )
