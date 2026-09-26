"""Launch the Claude Code CLI and capture its process result."""

import subprocess
import time
from pathlib import Path

from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime

from agentdocs.agents._jsonl import parse_jsonl_objects
from agentdocs.agents.base import AgentAdapter, AgentRunResult, AgentTimeoutError
from agentdocs.agents.failures import classify_agent_failure

_AGENT_NAME = "claude"
_EXECUTABLE_NAME = "claude"
_DEFAULT_TIMEOUT_SECONDS = 600
_MISSING_EXECUTABLE = (
    "Claude Code CLI executable 'claude' was not found in PATH. "
    "Install Claude Code and authenticate it before running AgentDocsBench."
)


class ClaudeAdapter(AgentAdapter):
    """Launch ``claude -p`` and capture stream-json output.

    File edits use ``--permission-mode acceptEdits``. Shell commands still
    follow the user's Claude Code settings. This adapter does not pass
    ``--dangerously-skip-permissions``.
    """

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
            (_EXECUTABLE_NAME,),
            missing_message=_MISSING_EXECUTABLE,
            agent_type=_AGENT_NAME,
        )
        command = _claude_command(
            executable,
            _build_prompt(task, docs_visible),
            docs_visible,
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
                f"Claude did not finish within {self.timeout_seconds:g} seconds "
                f"while running in {project_dir}."
            ) from exc
        duration_seconds = time.perf_counter() - started

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        events = parse_jsonl_objects(stdout, provider="Claude")
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


def _claude_command(
    executable: str,
    prompt: str,
    docs_path: str,
    model: str | None = None,
) -> list[str]:
    command = [executable, "-p"]
    if model is not None:
        command.extend(["--model", model])
    command.extend(
        [
            "--output-format",
            "stream-json",
            "--permission-mode",
            "acceptEdits",
            "--add-dir",
            docs_path,
            prompt,
        ]
    )
    return command


def _build_prompt(task: str, docs_path: str) -> str:
    return (
        "You are being evaluated by AgentDocsBench on whether you can complete "
        "a programming task using the provided product documentation.\n"
        "\n"
        "Task:\n"
        "\n"
        f"{task}\n"
        "\n"
        "Documentation is available at:\n"
        "\n"
        f"{docs_path}\n"
        "\n"
        "Requirements:\n"
        "- Read the documentation as needed.\n"
        "- Modify only the project in your current working directory.\n"
        "- Do not modify the documentation.\n"
        "- Do not modify files outside the AgentDocsBench workspace.\n"
        "- Do not commit or push.\n"
        "- Complete the task and stop when finished.\n"
    )


def _require_prompt(prompt: str) -> str:
    stripped = prompt.strip()
    if not stripped:
        raise ValueError("Task prompt must not be empty.")
    return stripped


def _require_directory(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label} directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{label} path is not a directory: {path}")
    return path
