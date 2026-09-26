"""Find the Codex CLI on PATH."""

import shutil

from agentdocs.agents.base import AgentExecutableNotFoundError

_EXECUTABLE_NAME = "codex"


def _require_codex_executable() -> str:
    executable = shutil.which(_EXECUTABLE_NAME)
    if executable is None:
        raise AgentExecutableNotFoundError(
            "Codex CLI executable 'codex' was not found in PATH. "
            "Install Codex CLI and authenticate it before running AgentDocsBench."
        )
    return executable
