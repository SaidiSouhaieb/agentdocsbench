"""Launch the Codex CLI and capture its process result."""

import subprocess
import time
from pathlib import Path

from agentdocs.agents.base import AgentAdapter, AgentRunResult, AgentTimeoutError
from agentdocs.agents.failures import classify_agent_failure
from agentdocs.agents.codex.build_prompt import _build_prompt
from agentdocs.agents.codex.codex_command import _codex_command
from agentdocs.agents.codex.parse_jsonl import _parse_jsonl
from agentdocs.agents.codex.require_directory import _require_directory
from agentdocs.agents.codex.require_prompt import _require_prompt
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime

_AGENT_NAME = "codex"
_DEFAULT_TIMEOUT_SECONDS = 600
_MISSING_EXECUTABLE = (
    "Codex CLI executable 'codex' was not found in PATH. "
    "Install Codex CLI and authenticate it before running AgentDocsBench."
)


class CodexAdapter(AgentAdapter):
    """Launch ``codex exec`` in non-interactive mode and capture its JSONL output."""

    def __init__(
        self,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        model: str | None = None,
        runtime: ExecutionRuntime | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.runtime = runtime if runtime is not None else LocalExecutionRuntime()

    def run(
        self,
        prompt: str,
        project_dir: Path,
        docs_dir: Path,
    ) -> AgentRunResult:
        task = _require_prompt(prompt)
        project_dir = _require_directory(project_dir, "project")
        docs_dir = _require_directory(docs_dir, "docs")
        _project_visible, docs_visible = self.runtime.agent_visible_paths(
            project_dir, docs_dir
        )
        executable = self.runtime.resolve_agent_executable(
            ("codex",),
            missing_message=_MISSING_EXECUTABLE,
            agent_type=_AGENT_NAME,
        )
        command = _codex_command(
            executable,
            _build_prompt(task, docs_visible),
            self.model,
        )

        started = time.perf_counter()
        try:
            completed = self.runtime.run_agent(
                command,
                host_project=project_dir,
                host_docs=docs_dir,
                timeout_seconds=self.timeout_seconds,
                agent_type=_AGENT_NAME,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentTimeoutError(
                f"Codex did not finish within {self.timeout_seconds:g} seconds "
                f"while running in {project_dir}."
            ) from exc
        duration_seconds = time.perf_counter() - started

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        events = _parse_jsonl(stdout)
        return AgentRunResult(
            agent=_AGENT_NAME,
            exit_code=completed.returncode,
            events=events,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            requested_model=self.model,
            failure=classify_agent_failure(
                provider=_AGENT_NAME,
                exit_code=completed.returncode,
                events=events,
                stdout=stdout,
                stderr=stderr,
            ),
        )
