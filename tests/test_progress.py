from __future__ import annotations

import shlex
import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from agentdocs import (
    AgentAdapter,
    AgentDocsConfig,
    AgentRunResult,
    AgentTimeoutError,
    TaskConfig,
    VerifierResult,
    VerifierTimeoutError,
    run_suite,
    run_task,
)
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.cli_progress import CliProgressReporter
from agentdocs.config import AgentConfig, VerifyConfig
from agentdocs.progress import (
    WorkspaceChangesCaptured,
    AgentFailed,
    AgentFinished,
    AgentStarted,
    SuiteFinished,
    SuiteStarted,
    TaskFinished,
    TaskStarted,
    VerifierFailed,
    VerifierFinished,
    VerifierStarted,
)

_CHECK = """\
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
user_file = project / "user.txt"
if not user_file.is_file() or user_file.read_text(encoding="utf-8").strip() != "Alice":
    print("missing Alice", file=sys.stderr)
    raise SystemExit(1)
print("user ok")
"""


class FakeAgent(AgentAdapter):
    def __init__(
        self,
        *,
        content: str = "Alice\n",
        exit_code: int = 0,
        error: BaseException | None = None,
        stdout: str = "agent-out\n",
        stderr: str = "",
    ) -> None:
        self.content = content
        self.exit_code = exit_code
        self.error = error
        self.stdout = stdout
        self.stderr = stderr

    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        if self.error is not None:
            raise self.error
        (project_dir / "user.txt").write_text(self.content, encoding="utf-8")
        return AgentRunResult(
            agent="cursor",
            exit_code=self.exit_code,
            events=({"type": "done"},),
            stdout=self.stdout,
            stderr=self.stderr,
            duration_seconds=1.5,
        )


def _config(tmp_path: Path, *task_ids: str) -> AgentDocsConfig:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    starter.mkdir()
    docs.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("Create Alice.\n", encoding="utf-8")
    tasks = []
    for task_id in task_ids:
        verifier = tmp_path / "verifiers" / task_id
        verifier.mkdir(parents=True)
        (verifier / "check.py").write_text(_CHECK, encoding="utf-8")
        tasks.append(
            TaskConfig(
                id=task_id,
                prompt=f"Do {task_id}.",
                verify=VerifyConfig(
                    path=verifier,
                    command=shlex.join([sys.executable, "check.py"]),
                ),
            )
        )
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="cursor"),
        tasks=tasks,
    )


def _names(events: list[object]) -> list[str]:
    return [type(event).__name__ for event in events]


def test_suite_emits_lifecycle_in_order(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user")
    events: list[object] = []

    result = run_suite(
        config,
        agent_factory=FakeAgent,
        on_progress=events.append,
    )

    assert result.passed is True
    assert _names(events) == [
        "SuiteStarted",
        "TaskStarted",
        "AgentStarted",
        "AgentFinished",
        "WorkspaceChangesCaptured",
        "VerifierStarted",
        "VerifierFinished",
        "TaskFinished",
        "SuiteFinished",
    ]
    started = events[2]
    finished = events[3]
    verified = events[6]
    task_finished = events[7]
    assert isinstance(started, AgentStarted)
    assert isinstance(finished, AgentFinished)
    assert isinstance(verified, VerifierFinished)
    assert isinstance(task_finished, TaskFinished)
    assert started.agent == "cursor"
    assert finished.result.exit_code == 0
    assert verified.result.passed is True
    assert task_finished.passed is True


def test_failed_verifier_still_finishes_the_task(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user", "create_project")
    events: list[object] = []
    calls = {"n": 0}

    def factory() -> FakeAgent:
        calls["n"] += 1
        content = "Alice\n" if calls["n"] == 1 else "Bob\n"
        return FakeAgent(content=content)

    result = run_suite(config, agent_factory=factory, on_progress=events.append)

    assert result.passed_count == 1
    assert result.failed_count == 1
    task_events = [event for event in events if isinstance(event, TaskFinished)]
    assert [event.passed for event in task_events] == [True, False]
    assert isinstance(events[-1], SuiteFinished)
    assert events[-1].failed == 1


def test_nonzero_agent_exit_can_still_pass(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user")
    events: list[object] = []

    result = run_task(
        config,
        config.tasks[0],
        agent=FakeAgent(exit_code=1),
        on_progress=events.append,
    )

    finished = next(event for event in events if isinstance(event, AgentFinished))
    task_finished = next(event for event in events if isinstance(event, TaskFinished))
    assert result.passed is True
    assert finished.result.exit_code == 1
    assert task_finished.passed is True


def test_agent_exception_emits_failure_and_propagates(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user")
    events: list[object] = []

    with pytest.raises(AgentTimeoutError, match="too slow"):
        run_task(
            config,
            config.tasks[0],
            agent=FakeAgent(error=AgentTimeoutError("too slow")),
            on_progress=events.append,
        )

    assert _names(events) == ["AgentStarted", "AgentFailed"]
    failed = events[1]
    assert isinstance(failed, AgentFailed)
    assert "too slow" in failed.error
    assert not any(isinstance(event, VerifierStarted) for event in events)


def test_verifier_exception_emits_failure_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, "create_user")
    events: list[object] = []

    def explode(*_args: object, **_kwargs: object) -> VerifierResult:
        raise VerifierTimeoutError("verifier timed out")

    monkeypatch.setattr("agentdocs.runner.run_verifier", explode)
    with pytest.raises(VerifierTimeoutError, match="verifier timed out"):
        run_task(
            config,
            config.tasks[0],
            agent=FakeAgent(),
            on_progress=events.append,
        )

    assert isinstance(events[-1], VerifierFailed)
    assert "verifier timed out" in events[-1].error
    assert not any(isinstance(event, TaskFinished) for event in events)


def test_missing_callback_preserves_results(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user")

    result = run_task(config, config.tasks[0], agent=FakeAgent())

    assert result.passed is True


def test_callback_exception_propagates(tmp_path: Path) -> None:
    config = _config(tmp_path, "create_user")

    def fail(_event: object) -> None:
        raise RuntimeError("reporter broke")

    with pytest.raises(RuntimeError, match="reporter broke"):
        run_task(config, config.tasks[0], agent=FakeAgent(), on_progress=fail)


def test_reporter_non_tty_lines() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=100)
    reporter = CliProgressReporter(console)
    reporter(SuiteStarted(total_tasks=1))
    reporter(TaskStarted(task_id="create_user", index=1, total=1))
    reporter(AgentStarted(task_id="create_user", agent="cursor"))
    reporter(
        AgentFinished(
            task_id="create_user",
            result=AgentRunResult(
                agent="cursor",
                exit_code=0,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=18.4,
            ),
        )
    )
    reporter(
        WorkspaceChangesCaptured(
            task_id="create_user",
            files_added=1,
            files_modified=0,
            files_deleted=0,
            directories_added=0,
            directories_deleted=0,
            type_changed=0,
            paths=("+ user.txt",),
        )
    )
    reporter(VerifierStarted(task_id="create_user"))
    reporter(
        VerifierFinished(
            task_id="create_user",
            result=VerifierResult(
                command=("python3", "check.py"),
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.1,
            ),
        )
    )
    reporter(TaskFinished(task_id="create_user", passed=True, duration_seconds=18.6))
    text = output.getvalue()
    assert "[1/1] create_user: agent started (cursor, provider default)" in text
    assert "agent exit 0 in 18.4s" in text
    assert "workspace changes +1 ~0 -0" in text
    assert "user.txt" not in text
    assert "verifier started" in text
    assert "PASS" in text
    assert "\x1b" not in text


def test_verbose_failure_metadata_omits_matched_text() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=120)
    reporter = CliProgressReporter(console, verbose=True, debug=True)
    reporter(TaskStarted(task_id="create_user", index=1, total=1))
    reporter(
        AgentFinished(
            task_id="create_user",
            result=AgentRunResult(
                agent="claude",
                exit_code=1,
                events=(),
                stdout="",
                stderr="Credit balance is too low\n",
                duration_seconds=0.8,
                failure=AgentFailureClassification(
                    schema_version=FAILURE_SCHEMA_VERSION,
                    kind=AgentFailureKind.quota_or_credit,
                    blocking=True,
                    rule_id="claude.credit_balance_low",
                    source="stderr",
                ),
            ),
        )
    )
    text = output.getvalue()
    assert "agent exit 1 in 0.8s · quota/credit" in text
    assert "kind: quota_or_credit" in text
    assert "blocking: yes" in text
    assert "rule: claude.credit_balance_low" in text
    assert "source: stderr" in text
    assert text.count("Credit balance is too low") == 1
    assert "\x1b" not in text


def test_reporter_quiet_prints_nothing() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    reporter = CliProgressReporter(console, quiet=True)
    reporter(TaskStarted(task_id="create_user", index=1, total=1))
    reporter(AgentStarted(task_id="create_user", agent="cursor"))
    assert output.getvalue() == ""
