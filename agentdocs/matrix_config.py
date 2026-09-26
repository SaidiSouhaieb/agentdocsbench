"""Load a matrix of explicit agent and model targets."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from agentdocs.config.constants import SUPPORTED_AGENT_TYPES
from agentdocs.config.errors import ConfigError
from agentdocs.config.format_validation_error import _format_validation_error

MATRIX_VERSIONS: frozenset[int] = frozenset({1})


class MatrixTargetConfig(BaseModel):
    """One explicit agent and optional model to run against a benchmark."""

    model_config = ConfigDict(extra="forbid")

    id: str
    agent: str
    model: str | None = None

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, target_id: str) -> str:
        stripped = target_id.strip()
        if not stripped:
            raise ValueError("Target id must not be empty.")
        return stripped

    @field_validator("agent")
    @classmethod
    def agent_must_be_supported(cls, agent: str) -> str:
        stripped = agent.strip()
        if not stripped:
            raise ValueError("Target agent must not be empty.")
        if stripped not in SUPPORTED_AGENT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_AGENT_TYPES))
            raise ValueError(
                f"Unsupported agent type {stripped!r}. "
                f"Supported agent types: {supported}."
            )
        return stripped

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, model: str | None) -> str | None:
        if model is None:
            return None
        stripped = model.strip()
        if not stripped:
            raise ValueError("Model must not be empty.")
        return stripped


class MatrixConfig(BaseModel):
    """Explicit targets for one benchmark. This file does not contain tasks."""

    model_config = ConfigDict(extra="forbid")

    version: int
    targets: list[MatrixTargetConfig]

    @field_validator("version")
    @classmethod
    def version_must_be_supported(cls, version: int) -> int:
        if version not in MATRIX_VERSIONS:
            supported = ", ".join(str(item) for item in sorted(MATRIX_VERSIONS))
            raise ValueError(
                f"Unsupported matrix version {version}. Supported versions: {supported}."
            )
        return version

    @field_validator("targets")
    @classmethod
    def targets_must_not_be_empty(
        cls, targets: list[MatrixTargetConfig]
    ) -> list[MatrixTargetConfig]:
        if not targets:
            raise ValueError("At least one matrix target is required.")
        return targets

    @model_validator(mode="after")
    def target_ids_must_be_unique(self) -> "MatrixConfig":
        seen: set[str] = set()
        duplicates: list[str] = []
        for target in self.targets:
            if target.id in seen and target.id not in duplicates:
                duplicates.append(target.id)
            seen.add(target.id)
        if duplicates:
            joined = ", ".join(repr(target_id) for target_id in duplicates)
            raise ValueError(
                f"Duplicate target id(s): {joined}. Target ids must be unique."
            )
        return self


def load_matrix_config(path: str | Path) -> MatrixConfig:
    """Load a matrix file. Paths inside the file are not resolved."""
    matrix_path = Path(path)
    if not matrix_path.exists():
        raise FileNotFoundError(f"Matrix file not found: {matrix_path}")
    if not matrix_path.is_file():
        raise ConfigError(f"Matrix path is not a file: {matrix_path}")
    data = _read_matrix_mapping(matrix_path)
    try:
        return MatrixConfig.model_validate(data)
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise ConfigError(
            f"Invalid matrix configuration in {matrix_path}:\n{details}"
        ) from exc


def _read_matrix_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"Matrix file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Matrix file must contain a YAML mapping, not {type(data).__name__}: {path}"
        )
    return data
