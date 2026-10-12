"""Next-step hints for common CLI failures.

Hints are attached only when the exception type identifies the problem.
They do not repeat provider output, credentials, or environment values.
"""

from __future__ import annotations

import re

from agentdocs.agents.base import AgentExecutableNotFoundError, AgentTimeoutError
from agentdocs.config.errors import ConfigError
from agentdocs.execution.errors import (
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
    RuntimeConfigError,
)
from agentdocs.experiment import ExperimentInvariantError
from agentdocs.github_actions import GitHubActionsError
from agentdocs.models import ModelDiscoveryError
from agentdocs.verifier.errors import (
    VerifierCommandError,
    VerifierExecutableNotFoundError,
    VerifierTimeoutError,
)
from agentdocs.workspace import WorkspaceError

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
_YAML_HINT = "Check the YAML syntax and indentation in the selected input file."
_VERSION_HINT = "Set `version` in agentdocs.yaml to a supported version shown in the error."
_AGENT_TYPE_HINT = "Set `agent.type` in agentdocs.yaml to a supported agent type shown in the error."
_MODEL_HINT = "Set `agent.model` in agentdocs.yaml to a non-empty model ID, or omit it to use the provider default."
_MODEL_OVERRIDE_HINT = "Pass a non-empty model ID to `--model`."
_TASK_ID_HINT = "Give each task in agentdocs.yaml a non-empty `id`."
_TASK_PROMPT_HINT = "Give each task in agentdocs.yaml a non-empty `prompt`."
_DUPLICATE_TASK_HINT = "Give each task in agentdocs.yaml a unique `id`."
_VERIFY_COMMAND_HINT = "Set each task's `verify.command` in agentdocs.yaml to a non-empty executable command."
_WORKSPACE_SYMLINK_HINT = "Remove symbolic links from the configured starter and docs directories."
_VERIFIER_COMMAND_HINT = "Check the task's `verify.command` in agentdocs.yaml for valid shell quoting and an executable."
_VERIFIER_EXECUTABLE_HINT = "Install the verifier executable or update `verify.command` to an executable on PATH."
_VERIFIER_TIMEOUT_HINT = "Check whether the verifier command hangs or needs a shorter workload."
_AGENT_TIMEOUT_HINT = "Check the provider CLI and task workload, then retry the benchmark."
_DISCOVERY_HINT = "Check the provider CLI installation and authentication, then retry `agentdocs models`."


def format_error_hint(exc: BaseException) -> str | None:
    """Return a next step for a known failure, or None when the cause is not specific."""
    if isinstance(exc, FileNotFoundError):
        return _file_hint(str(exc))
    if isinstance(exc, NotADirectoryError):
        return _file_hint(str(exc))
    if isinstance(exc, AgentExecutableNotFoundError):
        return _PROVIDER_HINT
    if isinstance(exc, AgentTimeoutError):
        return _AGENT_TIMEOUT_HINT
    if isinstance(exc, DockerExecutableNotFoundError):
        return _DOCKER_MISSING_HINT
    if isinstance(exc, DockerDaemonUnavailableError):
        return _DAEMON_HINT
    if isinstance(exc, DockerImageNotFoundError):
        return _IMAGE_HINT
    if isinstance(exc, RuntimeConfigError):
        if str(exc).startswith("Invalid YAML in "):
            return _YAML_HINT
        if "no agents." in str(exc):
            return _RUNTIME_AGENT_HINT
        return None
    if isinstance(exc, ConfigError):
        return _config_hint(str(exc))
    if isinstance(exc, ValueError) and str(exc) == "Model must not be empty.":
        return _MODEL_OVERRIDE_HINT
    if isinstance(exc, WorkspaceError) and str(exc).startswith("Workspace source contains symbolic link:"):
        return _WORKSPACE_SYMLINK_HINT
    if isinstance(exc, VerifierCommandError):
        return _VERIFIER_COMMAND_HINT
    if isinstance(exc, VerifierExecutableNotFoundError):
        return _VERIFIER_EXECUTABLE_HINT
    if isinstance(exc, VerifierTimeoutError):
        return _VERIFIER_TIMEOUT_HINT
    if isinstance(exc, ModelDiscoveryError):
        return _DISCOVERY_HINT
    if isinstance(exc, GitHubActionsError) and "GITHUB_ACTIONS" in str(exc):
        return _GITHUB_HINT
    if isinstance(exc, ExperimentInvariantError):
        return _INVARIANT_HINT
    return None


def _config_hint(message: str) -> str | None:
    if message.startswith("Invalid YAML in "):
        return _YAML_HINT
    if not message.startswith("Invalid configuration in "):
        return None
    for line in message.splitlines()[1:]:
        if line.startswith("version: Unsupported version "):
            return _VERSION_HINT
        if line.startswith("agent.type: Unsupported agent type "):
            return _AGENT_TYPE_HINT
        if line == "agent.model: Model must not be empty.":
            return _MODEL_HINT
        if re.fullmatch(r"tasks\.\d+\.id: Task id must not be empty\.", line):
            return _TASK_ID_HINT
        if re.fullmatch(r"tasks\.\d+\.prompt: Task prompt must not be empty\.", line):
            return _TASK_PROMPT_HINT
        if line.startswith("Duplicate task id(s):"):
            return _DUPLICATE_TASK_HINT
        if re.fullmatch(r"tasks\.\d+\.verify\.command: Verifier command must not be empty\.", line):
            return _VERIFY_COMMAND_HINT
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
