"""Next-step hints for common CLI failures.

Hints are attached only when the exception type identifies the problem.
They do not repeat provider output, credentials, or environment values.
"""

from __future__ import annotations

from agentdocs.agents.base import AgentExecutableNotFoundError
from agentdocs.execution.errors import (
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
    RuntimeConfigError,
)
from agentdocs.experiment import ExperimentInvariantError
from agentdocs.github_actions import GitHubActionsError

_CONFIG_HINT = (
    "Pass `--config <path>` or create a starter benchmark with `agentdocs init`."
)
_DOCS_HINT = (
    "Paths in agentdocs.yaml are resolved relative to that file. "
    "Check the configured `docs` path."
)
_STARTER_HINT = (
    "Paths in agentdocs.yaml are resolved relative to that file. "
    "Check the configured `starter` path."
)
_VERIFIER_HINT = (
    "verify.path is relative to the directory that contains agentdocs.yaml."
)
_PROVIDER_HINT = (
    "Install the provider CLI and make sure it is on PATH. "
    "Run `agentdocs doctor` to check the environment."
)
_DOCKER_MISSING_HINT = (
    "Install or start Docker, or run without `--runtime-config` to use local execution."
)
_DAEMON_HINT = "Make sure Docker is running, then run `agentdocs doctor --runtime-config <runtime.yaml>`."
_IMAGE_HINT = (
    "AgentDocsBench does not pull or build agent images automatically. "
    "Build or pull the configured image, then run `agentdocs doctor` to verify the runtime."
)
_RUNTIME_AGENT_HINT = "Add an entry for the selected agent under `agents:` in the runtime file."
_GITHUB_HINT = "Remove `--github-actions` for local runs."
_INVARIANT_HINT = (
    "Documentation experiments may vary only the documentation directory. "
    "Starter, tasks, prompts, and verifiers must remain unchanged."
)


def format_error_hint(exc: BaseException) -> str | None:
    """Return a next step for a known failure, or None when the cause is not specific."""
    if isinstance(exc, FileNotFoundError):
        return _file_hint(str(exc))
    if isinstance(exc, NotADirectoryError):
        return _file_hint(str(exc))
    if isinstance(exc, AgentExecutableNotFoundError):
        return _PROVIDER_HINT
    if isinstance(exc, DockerExecutableNotFoundError):
        return _DOCKER_MISSING_HINT
    if isinstance(exc, DockerDaemonUnavailableError):
        return _DAEMON_HINT
    if isinstance(exc, DockerImageNotFoundError):
        return _IMAGE_HINT
    if isinstance(exc, RuntimeConfigError):
        if "no agents." in str(exc):
            return _RUNTIME_AGENT_HINT
        return None
    if isinstance(exc, GitHubActionsError) and "GITHUB_ACTIONS" in str(exc):
        return _GITHUB_HINT
    if isinstance(exc, ExperimentInvariantError):
        return _INVARIANT_HINT
    return None


def _file_hint(message: str) -> str | None:
    if message.startswith("Config file not found"):
        return _CONFIG_HINT
    if message.startswith("docs directory") or message.startswith("docs path"):
        return _DOCS_HINT
    if message.startswith("starter "):
        return _STARTER_HINT
    if message.startswith("Verifier"):
        return _VERIFIER_HINT
    return None
