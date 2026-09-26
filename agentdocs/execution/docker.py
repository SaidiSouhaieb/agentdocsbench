"""Run the agent and the verifier in separate Docker containers.

Images are supplied by the user and must already exist locally. This module
does not pull, build, or install provider CLIs. It does not mount the Docker
socket, the host home directory, or provider credentials unless the runtime
config lists an explicit agent mount or environment name.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agentdocs.agents.base import AgentExecutableNotFoundError
from agentdocs.execution.errors import (
    DockerContainerError,
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
)
from agentdocs.execution.runtime_info import DockerImageInfo, RuntimeInfo
from agentdocs.runtime_config import AgentRuntimeConfig, RuntimeConfig, require_env_passthrough
from agentdocs.verifier.errors import VerifierExecutableNotFoundError

PROJECT_MOUNT = "/workspace/project"
DOCS_MOUNT = "/workspace/docs"
VERIFIER_MOUNT = "/workspace/verifier"
CONTAINER_HOME = "/tmp/agentdocs-home"
_PROBE_TIMEOUT_SECONDS = 30
_EXECUTABLE_MISSING = "executable file not found"
_DAEMON_MARKERS = (
    "Cannot connect to the Docker daemon",
    "Is the docker daemon running",
    "error during connect",
)


def agent_create_args(
    *,
    name: str,
    image: str,
    command: list[str],
    host_project: Path,
    host_docs: Path,
    network: str,
    extra_mounts: list[tuple[str, str, bool]],
    environment: dict[str, str],
) -> list[str]:
    """Build ``docker create`` argv for the coding agent.

    The project mount is read-write. The docs mount is read-only. The verifier
    tree is not mounted. ``command`` is the provider argv, passed without a shell.
    """
    args = _create_prefix(name=name, network=network, workdir=PROJECT_MOUNT)
    args.extend(["-e", f"HOME={CONTAINER_HOME}"])
    for key, value in environment.items():
        args.extend(["-e", f"{key}={value}"])
    args.extend(
        [
            "--mount",
            _bind(host_project, PROJECT_MOUNT, read_only=False),
            "--mount",
            _bind(host_docs, DOCS_MOUNT, read_only=True),
        ]
    )
    for source, target, read_only in extra_mounts:
        args.extend(["--mount", _bind(Path(source), target, read_only=read_only)])
    _append_command(args, image, command)
    return args


def verifier_create_args(
    *,
    name: str,
    image: str,
    command: list[str],
    host_project: Path,
    host_verifier: Path,
) -> list[str]:
    """Build ``docker create`` argv for the verifier.

    Network is ``none``. Docs, agent mounts, and agent environment variables
    are not included. ``AGENTDOCS_PROJECT_DIR`` is the container project path.
    """
    args = _create_prefix(name=name, network="none", workdir=VERIFIER_MOUNT)
    args.extend(
        [
            "-e",
            f"HOME={CONTAINER_HOME}",
            "-e",
            f"AGENTDOCS_PROJECT_DIR={PROJECT_MOUNT}",
            "--mount",
            _bind(host_project, PROJECT_MOUNT, read_only=False),
            "--mount",
            _bind(host_verifier, VERIFIER_MOUNT, read_only=False),
        ]
    )
    _append_command(args, image, command)
    return args


def probe_create_args(*, name: str, image: str, executable: str) -> list[str]:
    """Build a no-mount probe that checks whether ``executable`` can start."""
    args = _create_prefix(name=name, network="none", workdir="/tmp")
    args.extend(["-e", f"HOME={CONTAINER_HOME}"])
    _append_command(args, image, [executable, "--version"])
    return args


class DockerExecutionRuntime:
    """One Docker runtime for every task in an invocation.

    Image ids are cached on this object. The cache is not shared across
    processes and is not written to disk.
    """

    backend = "docker"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._image_ids: dict[str, str] = {}
        self._executables: dict[tuple[str, str, tuple[str, ...]], str] = {}

    def prepare(self, agent_type: str) -> None:
        """Require the agent entry, its env names, and both local images."""
        spec = self._agent_spec(agent_type)
        require_env_passthrough(spec.env_passthrough, agent_type=agent_type)
        self.image_id(spec.image)
        self.image_id(self.config.verifier.image)

    def agent_visible_paths(self, project_dir: Path, docs_dir: Path) -> tuple[str, str]:
        """Prompts and ``--add-dir`` use container paths, not host temp paths."""
        del project_dir, docs_dir
        return PROJECT_MOUNT, DOCS_MOUNT

    def resolve_agent_executable(
        self,
        candidates: tuple[str, ...],
        *,
        missing_message: str,
        agent_type: str,
    ) -> str:
        """Return the first candidate that exists inside the agent image.

        The host PATH is not consulted. A missing binary is an infrastructure
        error, not ``unknown_agent_failure``.
        """
        del missing_message
        spec = self._agent_spec(agent_type)
        key = (agent_type, spec.image, candidates)
        cached = self._executables.get(key)
        if cached is not None:
            return cached
        require_env_passthrough(spec.env_passthrough, agent_type=agent_type)
        self.image_id(spec.image)
        for name in candidates:
            if self._image_has_executable(spec.image, name):
                self._executables[key] = name
                return name
        joined = ", ".join(repr(name) for name in candidates)
        raise AgentExecutableNotFoundError(
            f"None of {joined} was found in Docker image {spec.image!r}. "
            "AgentDocsBench does not install provider CLIs into images."
        )

    def run_agent(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_docs: Path,
        timeout_seconds: float,
        agent_type: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the provider CLI in its container and return its exit code."""
        spec = self._agent_spec(agent_type)
        environment = require_env_passthrough(spec.env_passthrough, agent_type=agent_type)
        extra = [
            (mount.source, mount.target, mount.read_only) for mount in spec.mounts
        ]
        name = _container_name("agent")
        args = agent_create_args(
            name=name,
            image=spec.image,
            command=command,
            host_project=host_project,
            host_docs=host_docs,
            network=spec.network,
            extra_mounts=extra,
            environment=environment,
        )
        return self._run_container(
            name=name,
            create_args=args,
            inner_command=command,
            timeout_seconds=timeout_seconds,
            role="agent",
        )

    def run_verifier(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_verifier: Path,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run the verifier in a second container with no network and no docs."""
        name = _container_name("verifier")
        args = verifier_create_args(
            name=name,
            image=self.config.verifier.image,
            command=command,
            host_project=host_project,
            host_verifier=host_verifier,
        )
        return self._run_container(
            name=name,
            create_args=args,
            inner_command=command,
            timeout_seconds=timeout_seconds,
            role="verifier",
        )

    def provenance(self, agent_type: str) -> RuntimeInfo:
        """Record requested image names, resolved ids, and network modes."""
        spec = self._agent_spec(agent_type)
        return RuntimeInfo(
            schema_version=1,
            backend="docker",
            agent=DockerImageInfo(
                requested_image=spec.image,
                image_id=self.image_id(spec.image),
                network=spec.network,
            ),
            verifier=DockerImageInfo(
                requested_image=self.config.verifier.image,
                image_id=self.image_id(self.config.verifier.image),
                network="none",
            ),
        )

    def status_lines(self, agent_type: str, *, verbose: bool) -> list[str]:
        """Header lines for one agent type. Image ids are included only when verbose."""
        spec = self._agent_spec(agent_type)
        lines = [
            "Runtime: docker",
            f"Agent image: {spec.image}",
            f"Verifier image: {self.config.verifier.image}",
        ]
        if not verbose:
            return lines
        info = self.provenance(agent_type)
        assert info.agent is not None and info.verifier is not None
        lines.extend(
            [
                "Runtime backend: docker",
                f"Agent image ID: {info.agent.image_id}",
                f"Verifier image ID: {info.verifier.image_id}",
                f"Agent network: {info.agent.network}",
                "Verifier network: none",
            ]
        )
        return lines

    def image_id(self, requested: str) -> str:
        """Inspect a local image once per runtime object. This does not pull."""
        cached = self._image_ids.get(requested)
        if cached is not None:
            return cached
        completed = self._docker(["docker", "image", "inspect", requested])
        if completed.returncode != 0:
            self._raise_command_failure(completed, action=f"inspect image {requested}")
        try:
            payload = json.loads(completed.stdout or "")
            image_id = payload[0]["Id"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise DockerContainerError(
                f"Docker image inspect for {requested} did not return an image id."
            ) from exc
        if not isinstance(image_id, str) or not image_id:
            raise DockerContainerError(
                f"Docker image inspect for {requested} did not return an image id."
            )
        self._image_ids[requested] = image_id
        return image_id

    def _agent_spec(self, agent_type: str) -> AgentRuntimeConfig:
        return self.config.agents.for_agent(agent_type)

    def _image_has_executable(self, image: str, executable: str) -> bool:
        name = _container_name("probe")
        args = probe_create_args(name=name, image=image, executable=executable)
        try:
            self._run_container(
                name=name,
                create_args=args,
                inner_command=[executable, "--version"],
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
                role="probe",
            )
        except AgentExecutableNotFoundError:
            return False
        return True

    def _run_container(
        self,
        *,
        name: str,
        create_args: list[str],
        inner_command: list[str],
        timeout_seconds: float,
        role: str,
    ) -> subprocess.CompletedProcess[str]:
        created = False
        succeeded = False
        try:
            created_process = self._docker(create_args)
            if created_process.returncode != 0:
                self._raise_command_failure(created_process, action=f"create the {role} container")
            created = True
            try:
                started = self._docker(["docker", "start", "-a", name], timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill(name)
                raise
            result = self._process_result(name, started, inner_command, role)
            succeeded = True
            return result
        finally:
            if created:
                removed = self._docker(["docker", "rm", "-f", name])
                if succeeded and removed.returncode != 0:
                    raise DockerContainerError(
                        f"Docker could not remove container {name} after the {role} "
                        "process finished, so the result was discarded."
                    )

    def _process_result(
        self,
        name: str,
        started: subprocess.CompletedProcess[str],
        inner_command: list[str],
        role: str,
    ) -> subprocess.CompletedProcess[str]:
        inspected = self._docker(["docker", "inspect", name])
        if inspected.returncode != 0:
            self._raise_command_failure(inspected, action=f"inspect the {role} container")
        state = _state(inspected.stdout, name)
        error = state.get("Error") if isinstance(state.get("Error"), str) else ""
        oom = bool(state.get("OOMKilled"))
        combined = f"{error}\n{started.stderr or ''}"
        if _EXECUTABLE_MISSING in combined:
            if role == "verifier":
                raise VerifierExecutableNotFoundError(
                    f"Verifier executable {inner_command[0]!r} was not found in the "
                    "verifier image. AgentDocsBench does not install verifier tools."
                )
            raise AgentExecutableNotFoundError(
                f"Executable {inner_command[0]!r} was not found in the Docker image. "
                "AgentDocsBench does not install provider CLIs into images."
            )
        if oom:
            raise DockerContainerError(
                f"The {role} container was killed because it ran out of memory (OOMKilled)."
            )
        if error:
            raise DockerContainerError(
                f"The {role} container did not start: {_snippet(error)}"
            )
        exit_code = state.get("ExitCode")
        if not isinstance(exit_code, int):
            raise DockerContainerError(
                f"Docker inspect did not report an exit code for the {role} container."
            )
        return subprocess.CompletedProcess(
            inner_command,
            exit_code,
            stdout=started.stdout or "",
            stderr=started.stderr or "",
        )

    def _kill(self, name: str) -> None:
        """Best-effort stop. A kill failure must not replace the caller's timeout."""
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "kill", name],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )

    def _docker(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self._require_docker()
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise
        self._raise_if_daemon(completed)
        return completed

    def _require_docker(self) -> None:
        if shutil.which("docker") is None:
            raise DockerExecutableNotFoundError(
                "Docker CLI executable 'docker' was not found in PATH. "
                "Install Docker to use a runtime config. Local execution does not need it."
            )

    def _raise_if_daemon(self, completed: subprocess.CompletedProcess[str]) -> None:
        if completed.returncode == 0:
            return
        text = f"{completed.stderr or ''}\n{completed.stdout or ''}"
        if any(marker in text for marker in _DAEMON_MARKERS):
            raise DockerDaemonUnavailableError(
                "Docker is installed but the daemon is not available. "
                f"{_snippet(completed.stderr or completed.stdout or '')}"
            )

    def _raise_command_failure(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        action: str,
    ) -> None:
        self._raise_if_daemon(completed)
        text = f"{completed.stderr or ''}\n{completed.stdout or ''}"
        if "No such image" in text or "pull access denied" in text:
            requested = _requested_image(completed.args)
            raise DockerImageNotFoundError(
                f"Docker image {requested!r} is not available locally. "
                "AgentDocsBench does not pull or build images. "
                "Install it yourself with docker pull or docker build, then retry."
            )
        raise DockerContainerError(
            f"Docker could not {action}: {_snippet(completed.stderr or completed.stdout or '')}"
        )


def _create_prefix(*, name: str, network: str, workdir: str) -> list[str]:
    args = [
        "docker",
        "create",
        "--name",
        name,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        network,
    ]
    args.extend(_user_args())
    args.extend(
        [
            "--tmpfs",
            "/tmp:rw,nosuid,nodev",
            "-w",
            workdir,
        ]
    )
    return args


def _user_args() -> list[str]:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if not callable(getuid) or not callable(getgid):
        return []
    return ["--user", f"{getuid()}:{getgid()}"]


def _bind(source: Path, target: str, *, read_only: bool) -> str:
    spec = f"type=bind,source={source},target={target}"
    if read_only:
        spec += ",readonly"
    return spec


def _append_command(args: list[str], image: str, command: list[str]) -> None:
    """Place provider argv after the image with an explicit entrypoint.

    ``--entrypoint`` is the executable. The remaining adapter arguments follow
    the image name. Nothing is joined into a shell string.
    """
    if not command:
        raise DockerContainerError("Container command must not be empty.")
    args.extend(["--entrypoint", command[0], image, *command[1:]])


def _container_name(role: str) -> str:
    return f"agentdocs-{role}-{uuid.uuid4().hex[:12]}"


def _state(stdout: str, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout or "")
        state = payload[0]["State"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise DockerContainerError(
            f"Docker inspect for {name} did not return container state."
        ) from exc
    if not isinstance(state, dict):
        raise DockerContainerError(f"Docker inspect for {name} did not return container state.")
    return state


def _requested_image(args: object) -> str:
    if isinstance(args, list):
        for index, item in enumerate(args):
            if item == "inspect" and index + 1 < len(args):
                return str(args[index + 1])
            if item == "--entrypoint" and index + 2 < len(args):
                return str(args[index + 2])
    return "the configured image"


def _snippet(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 400:
        return compact or "no Docker diagnostic was returned"
    return compact[:397] + "..."
