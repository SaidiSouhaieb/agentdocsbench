from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from agentdocs import (
    AgentAdapter,
    AgentDocsConfig,
    AgentError,
    AgentExecutableNotFoundError,
    AgentRunResult,
    CodexAdapter,
    TaskConfig,
    TaskRunResult,
    VerifierExecutableNotFoundError,
    create_agent_adapter,
    run_task,
)
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.config import AgentConfig, VerifyConfig

_QUOTA = AgentFailureClassification(
    schema_version=FAILURE_SCHEMA_VERSION,
    kind=AgentFailureKind.quota_or_credit,
    blocking=True,
    rule_id="claude.credit_balance_low",
    source="stderr",
)
_UNKNOWN = AgentFailureClassification(
    schema_version=FAILURE_SCHEMA_VERSION,
    kind=AgentFailureKind.unknown_agent_failure,
    blocking=False,
    rule_id="unmatched_nonzero_exit",
    source="exit_code",
)

_CHECK = """\
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
user_file = project / "user.txt"
if not user_file.is_file():
    print("missing user.txt", file=sys.stderr)
    raise SystemExit(1)
content = user_file.read_text(encoding="utf-8").strip()
if content != "Alice":
    print(f"expected Alice, found {content}", file=sys.stderr)
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
        failure: AgentFailureClassification | None = None,
        starter: Path | None = None,
        docs: Path | None = None,
    ) -> None:
        self.content = content
        self.exit_code = exit_code
        self.error = error
        self.failure = failure
        self.starter = starter
        self.docs = docs
        self.calls: list[tuple[str, Path, Path]] = []
        self.last_project_dir: Path | None = None
        self.last_docs_dir: Path | None = None

    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        self.calls.append((prompt, project_dir, docs_dir))
        self.last_project_dir = project_dir
        self.last_docs_dir = docs_dir
        if self.starter is not None:
            assert project_dir != self.starter
            assert (project_dir / "app.py").read_text(encoding="utf-8") == (
                self.starter / "app.py"
            ).read_text(encoding="utf-8")
        if self.docs is not None:
            assert docs_dir != self.docs
            assert (docs_dir / "users.md").is_file()
        if self.error is not None:
            raise self.error
        (project_dir / "user.txt").write_text(self.content, encoding="utf-8")
        return AgentRunResult(
            agent="fake",
            exit_code=self.exit_code,
            events=(),
            stdout="agent stdout",
            stderr="agent stderr",
            duration_seconds=0.01,
            failure=self.failure,
        )


def _benchmark(tmp_path: Path, *, command: str | None = None) -> tuple[AgentDocsConfig, TaskConfig, Path, Path]:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifiers" / "create_user"
    starter.mkdir()
    docs.mkdir()
    verifier.mkdir(parents=True)
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("Create user.txt containing Alice.\n", encoding="utf-8")
    (verifier / "check.py").write_text(_CHECK, encoding="utf-8")
    if command is None:
        command = shlex.join([sys.executable, "check.py"])
    config = AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="codex"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create a user named Alice.",
                verify=VerifyConfig(path=verifier, command=command),
            )
        ],
    )
    return config, config.tasks[0], starter, docs


def test_correct_task_passes(tmp_path: Path) -> None:
    config, task, starter, docs = _benchmark(tmp_path)
    agent = FakeAgent(starter=starter, docs=docs)

    result = run_task(config, task, agent=agent)

    assert result.passed is True
    assert result.verifier_result.exit_code == 0
    assert "user ok" in result.verifier_result.stdout


def test_incorrect_task_fails_without_raising(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(content="Bob\n")

    result = run_task(config, task, agent=agent)

    assert result.passed is False
    assert result.verifier_result.exit_code != 0
    assert "Bob" in result.verifier_result.stderr


def test_agent_receives_task_prompt(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent()

    run_task(config, task, agent=agent)

    assert agent.calls[0][0] == "Create a user named Alice."


def test_agent_receives_workspace_project_not_starter(tmp_path: Path) -> None:
    config, task, starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(starter=starter)

    run_task(config, task, agent=agent)

    assert agent.last_project_dir is not None
    assert agent.last_project_dir != starter.resolve()
    assert agent.last_project_dir.name == "project"


def test_agent_receives_workspace_docs_not_original_docs(tmp_path: Path) -> None:
    config, task, _starter, docs = _benchmark(tmp_path)
    agent = FakeAgent(docs=docs)

    run_task(config, task, agent=agent)

    assert agent.last_docs_dir is not None
    assert agent.last_docs_dir != docs.resolve()
    assert agent.last_docs_dir.name == "docs"


def test_starter_files_are_present_before_agent_writes(tmp_path: Path) -> None:
    config, task, starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(starter=starter)

    run_task(config, task, agent=agent)

    assert agent.calls


def test_docs_are_present_before_agent_runs(tmp_path: Path) -> None:
    config, task, _starter, docs = _benchmark(tmp_path)
    agent = FakeAgent(docs=docs)

    run_task(config, task, agent=agent)

    assert (docs / "users.md").is_file()


def test_agent_modifications_are_visible_to_verifier(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent(content="Alice\n"))

    assert result.verifier_result.passed is True


def test_original_starter_is_unchanged(tmp_path: Path) -> None:
    config, task, starter, _docs = _benchmark(tmp_path)
    original = (starter / "app.py").read_text(encoding="utf-8")

    run_task(config, task, agent=FakeAgent())

    assert (starter / "app.py").read_text(encoding="utf-8") == original
    assert not (starter / "user.txt").exists()


def test_original_docs_are_unchanged(tmp_path: Path) -> None:
    config, task, _starter, docs = _benchmark(tmp_path)
    original = (docs / "users.md").read_text(encoding="utf-8")

    run_task(config, task, agent=FakeAgent())

    assert (docs / "users.md").read_text(encoding="utf-8") == original


def test_result_contains_task_id_and_both_results(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent())

    assert result.task_id == "create_user"
    assert isinstance(result, TaskRunResult)
    assert isinstance(result.agent_result, AgentRunResult)
    assert result.agent_result.stdout == "agent stdout"
    assert result.agent_result.stderr == "agent stderr"
    assert result.verifier_result.command[0] == sys.executable


def test_duration_is_non_negative(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent())

    assert result.duration_seconds >= 0


def test_passed_follows_verifier(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent(content="Bob\n"))

    assert result.passed is result.verifier_result.passed
    assert result.passed is False


def test_agent_completed_cleanly_for_exit_code_zero(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent(exit_code=0))

    assert result.agent_completed_cleanly is True
    assert result.agent_result.exit_code == 0


def test_nonzero_agent_exit_still_passes_when_project_is_correct(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent(content="Alice\n", exit_code=1))

    assert result.agent_completed_cleanly is False
    assert result.passed is True


def test_nonzero_agent_exit_fails_when_project_is_incorrect(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(config, task, agent=FakeAgent(content="Bob\n", exit_code=1))

    assert result.agent_completed_cleanly is False
    assert result.passed is False


def test_agent_infrastructure_error_propagates_without_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    verifier_calls: list[object] = []

    def fail_verifier(*_args: object, **_kwargs: object) -> None:
        verifier_calls.append(True)
        raise AssertionError("verifier should not run")

    monkeypatch.setattr("agentdocs.runner.run_verifier", fail_verifier)
    agent = FakeAgent(error=AgentExecutableNotFoundError("codex missing"))

    with pytest.raises(AgentExecutableNotFoundError, match="codex missing"):
        run_task(config, task, agent=agent)

    assert verifier_calls == []
    assert agent.last_project_dir is not None
    assert not agent.last_project_dir.exists()


def test_workspace_cleans_up_when_agent_raises(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(error=AgentError("agent exploded"))

    with pytest.raises(AgentError, match="agent exploded"):
        run_task(config, task, agent=agent)

    assert agent.last_project_dir is not None
    assert not agent.last_project_dir.exists()
    assert not agent.last_project_dir.parent.exists()


def test_verifier_infrastructure_error_propagates(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(
        tmp_path,
        command="imaginary-test-runner check.py",
    )
    agent = FakeAgent()

    with pytest.raises(VerifierExecutableNotFoundError, match="imaginary-test-runner"):
        run_task(config, task, agent=agent)

    assert agent.last_project_dir is not None
    assert not agent.last_project_dir.exists()


def test_workspace_cleans_up_when_verifier_raises(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(
        tmp_path,
        command="imaginary-test-runner check.py",
    )
    agent = FakeAgent()

    with pytest.raises(VerifierExecutableNotFoundError):
        run_task(config, task, agent=agent)

    assert agent.last_docs_dir is not None
    assert not agent.last_docs_dir.exists()


def test_workspace_cleans_up_after_pass(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(content="Alice\n")

    result = run_task(config, task, agent=agent)

    assert result.passed is True
    assert agent.last_project_dir is not None
    assert not agent.last_project_dir.exists()


def test_workspace_cleans_up_after_fail(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    agent = FakeAgent(content="Bob\n")

    result = run_task(config, task, agent=agent)

    assert result.passed is False
    assert agent.last_project_dir is not None
    assert not agent.last_project_dir.exists()


def test_default_agent_factory_returns_codex_adapter() -> None:
    adapter = create_agent_adapter("codex")

    assert isinstance(adapter, CodexAdapter)


def test_default_agent_factory_returns_claude_and_cursor() -> None:
    from agentdocs import ClaudeAdapter, CursorAdapter

    assert isinstance(create_agent_adapter("claude"), ClaudeAdapter)
    assert isinstance(create_agent_adapter("cursor"), CursorAdapter)


def test_factory_forwards_requested_model() -> None:
    from agentdocs import ClaudeAdapter, CursorAdapter

    codex = create_agent_adapter("codex", model="model-a")
    claude = create_agent_adapter("claude", model="model-b")
    cursor = create_agent_adapter("cursor", model="model-c")

    assert isinstance(codex, CodexAdapter)
    assert isinstance(claude, ClaudeAdapter)
    assert isinstance(cursor, CursorAdapter)
    assert codex.model == "model-a"
    assert claude.model == "model-b"
    assert cursor.model == "model-c"


def test_default_agent_factory_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported agent type 'gemini'"):
        create_agent_adapter("gemini")


def test_run_task_uses_factory_when_agent_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, task, starter, docs = _benchmark(tmp_path)
    created: list[str] = []

    class StubCodex(CodexAdapter):
        def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
            created.append("codex")
            (project_dir / "user.txt").write_text("Alice\n", encoding="utf-8")
            assert project_dir != starter
            assert docs_dir != docs
            return AgentRunResult(
                agent="codex",
                exit_code=0,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

    monkeypatch.setattr("agentdocs.agents.factory.CodexAdapter", StubCodex)

    result = run_task(config, task)

    assert created == ["codex"]
    assert result.passed is True
    assert result.agent_result.agent == "codex"


def test_explicit_agent_overrides_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    def fail_factory(_agent_type: str) -> CodexAdapter:
        raise AssertionError("factory should not be called")

    monkeypatch.setattr("agentdocs.runner.create_agent_adapter", fail_factory)
    agent = FakeAgent()

    result = run_task(config, task, agent=agent)

    assert result.passed is True
    assert result.agent_result.agent == "fake"


def test_invalid_verifier_timeout_raises(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    with pytest.raises(ValueError, match="verifier_timeout_seconds must be greater than 0"):
        run_task(config, task, agent=FakeAgent(), verifier_timeout_seconds=0)


def test_unknown_task_id_is_rejected(tmp_path: Path) -> None:
    config, _task, _starter, docs = _benchmark(tmp_path)
    other = TaskConfig(
        id="missing_task",
        prompt="Do something else.",
        verify=config.tasks[0].verify,
    )

    with pytest.raises(ValueError, match="Task 'missing_task' is not present"):
        run_task(config, other, agent=FakeAgent())

    assert docs.is_dir()


_VERIFIER_SIDE_EFFECT = """\
import os
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
(project / "verifier-only.txt").write_text("from-verifier", encoding="utf-8")
raise SystemExit(0)
"""


class EditingAgent(AgentAdapter):
    def __init__(
        self,
        *,
        exit_code: int = 0,
        failure: AgentFailureClassification | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.failure = failure
        self.names_during_run: list[str] = []
        self.project_dir: Path | None = None

    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        self.project_dir = project_dir
        self.names_during_run = sorted(path.name for path in project_dir.iterdir())
        (project_dir / "app.py").write_text("changed by agent\n", encoding="utf-8")
        (project_dir / "old.txt").unlink()
        (project_dir / "agent.txt").write_text("from-agent\n", encoding="utf-8")
        (project_dir / "generated").mkdir()
        return AgentRunResult(
            agent="fake",
            exit_code=self.exit_code,
            events=(),
            stdout="",
            stderr="",
            duration_seconds=0.01,
            failure=self.failure,
        )


def test_verifier_file_is_excluded_from_agent_changes(tmp_path: Path) -> None:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifier"
    starter.mkdir()
    docs.mkdir()
    verifier.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (starter / "old.txt").write_text("remove me\n", encoding="utf-8")
    (docs / "users.md").write_text("docs\n", encoding="utf-8")
    (verifier / "check.py").write_text(_VERIFIER_SIDE_EFFECT, encoding="utf-8")
    config = AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="cursor"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Edit the project.",
                verify=VerifyConfig(
                    path=verifier,
                    command=shlex.join([sys.executable, "check.py"]),
                ),
            )
        ],
    )
    agent = EditingAgent(exit_code=1, failure=_QUOTA)

    result = run_task(config, config.tasks[0], agent=agent)

    added = [item.path for item in result.workspace_changes.added_files]
    assert result.passed is True
    assert result.agent_result.exit_code == 1
    assert result.agent_result.failure == _QUOTA
    assert "agent.txt" in added
    assert "verifier-only.txt" not in added
    assert [item.path for item in result.workspace_changes.modified_files] == ["app.py"]
    assert [item.path for item in result.workspace_changes.deleted_files] == ["old.txt"]
    assert result.workspace_changes.added_directories == ("generated",)
    assert "verifier-only.txt" not in agent.names_during_run
    assert agent.project_dir is not None
    assert not agent.project_dir.exists()
    assert not (starter / "agent.txt").exists()


def test_snapshots_bracket_the_agent_and_precede_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentdocs.runner as runner_module

    config, task, _starter, _docs = _benchmark(tmp_path)
    order: list[str] = []
    real_capture = runner_module.capture_workspace_snapshot
    real_compare = runner_module.compare_workspace_snapshots
    real_verifier = runner_module.run_verifier

    def capture(path: Path):
        order.append("snapshot")
        return real_capture(path)

    def compare(before: object, after: object):
        order.append("compare")
        return real_compare(before, after)

    def verify(*args: object, **kwargs: object):
        order.append("verifier")
        return real_verifier(*args, **kwargs)

    class OrderAgent(AgentAdapter):
        def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
            order.append("agent")
            (project_dir / "user.txt").write_text("Alice\n", encoding="utf-8")
            return AgentRunResult(
                agent="fake",
                exit_code=0,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=0.01,
            )

    monkeypatch.setattr(runner_module, "capture_workspace_snapshot", capture)
    monkeypatch.setattr(runner_module, "compare_workspace_snapshots", compare)
    monkeypatch.setattr(runner_module, "run_verifier", verify)

    result = run_task(config, task, agent=OrderAgent())

    assert order == ["snapshot", "agent", "snapshot", "compare", "verifier"]
    assert [item.path for item in result.workspace_changes.added_files] == ["user.txt"]


def test_fresh_workspaces_do_not_leak_changes_between_tasks(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)
    second_task = task.model_copy(update={"id": "create_project"})
    config = config.model_copy(update={"tasks": [task, second_task]})

    class NamedAgent(AgentAdapter):
        def __init__(self, name: str) -> None:
            self.name = name

        def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
            (project_dir / f"{self.name}.txt").write_text(self.name, encoding="utf-8")
            return AgentRunResult(
                agent="fake",
                exit_code=0,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=0.01,
            )

    first = run_task(config, task, agent=NamedAgent("alpha"))
    second = run_task(config, second_task, agent=NamedAgent("beta"))

    assert [item.path for item in first.workspace_changes.added_files] == ["alpha.txt"]
    assert [item.path for item in second.workspace_changes.added_files] == ["beta.txt"]
    assert second.workspace_changes.deleted_files == ()


def test_blocking_failure_and_verifier_failure_are_both_kept(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(
        config,
        task,
        agent=FakeAgent(content="Bob\n", exit_code=1, failure=_QUOTA),
    )

    assert result.passed is False
    assert result.agent_result.failure is not None
    assert result.agent_result.failure.blocking is True
    assert [item.path for item in result.workspace_changes.added_files] == ["user.txt"]


def test_unknown_agent_failure_can_still_pass(tmp_path: Path) -> None:
    config, task, _starter, _docs = _benchmark(tmp_path)

    result = run_task(
        config,
        task,
        agent=FakeAgent(content="Alice\n", exit_code=1, failure=_UNKNOWN),
    )

    assert result.passed is True
    assert result.agent_result.failure is not None
    assert result.agent_result.failure.blocking is False
