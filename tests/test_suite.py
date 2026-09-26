from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from agentdocs import (
    AgentAdapter,
    AgentDocsConfig,
    AgentError,
    AgentRunResult,
    SuiteRunResult,
    TaskConfig,
    TaskRunResult,
    VerifierExecutableNotFoundError,
    VerifierResult,
    run_suite,
)
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.config import AgentConfig, VerifyConfig

_CHECK = """\
from pathlib import Path
import os
import sys

expected = Path("expected.txt").read_text(encoding="utf-8").strip()
project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
result = project / "result.txt"
if not result.is_file():
    print("missing result.txt", file=sys.stderr)
    raise SystemExit(1)
content = result.read_text(encoding="utf-8").strip()
if content != expected:
    print(f"expected {expected}, found {content}", file=sys.stderr)
    raise SystemExit(1)
print(expected)
"""


class FakeAgent(AgentAdapter):
    def __init__(
        self,
        content: str,
        *,
        exit_code: int = 0,
        error: BaseException | None = None,
        failure: AgentFailureClassification | None = None,
        leak: bool = False,
        reject_leak: bool = False,
    ) -> None:
        self.content = content
        self.exit_code = exit_code
        self.error = error
        self.failure = failure
        self.leak = leak
        self.reject_leak = reject_leak
        self.project_dir: Path | None = None
        self.saw_leak = False

    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        self.project_dir = project_dir
        self.saw_leak = (project_dir / "leaked.txt").exists()
        if self.reject_leak and self.saw_leak:
            raise AssertionError("workspace leaked leaked.txt into the next task")
        if self.error is not None:
            raise self.error
        (project_dir / "result.txt").write_text(self.content, encoding="utf-8")
        if self.leak:
            (project_dir / "leaked.txt").write_text("leak\n", encoding="utf-8")
        return AgentRunResult(
            agent="fake",
            exit_code=self.exit_code,
            events=(),
            stdout=prompt,
            stderr="",
            duration_seconds=0.0,
            failure=self.failure,
        )


class ScriptedFactory:
    def __init__(self, scripts: list[FakeAgent]) -> None:
        self.scripts = scripts
        self.calls = 0
        self.agents: list[FakeAgent] = []

    def __call__(self) -> FakeAgent:
        agent = self.scripts[self.calls]
        self.calls += 1
        self.agents.append(agent)
        return agent


def _suite(tmp_path: Path) -> tuple[AgentDocsConfig, Path, Path]:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    starter.mkdir()
    docs.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("Write result.txt.\n", encoding="utf-8")
    command = shlex.join([sys.executable, "check.py"])
    tasks: list[TaskConfig] = []
    for task_id, expected in (
        ("task_one", "ONE"),
        ("task_two", "TWO"),
        ("task_three", "THREE"),
    ):
        verifier = tmp_path / "verifiers" / task_id
        verifier.mkdir(parents=True)
        (verifier / "check.py").write_text(_CHECK, encoding="utf-8")
        (verifier / "expected.txt").write_text(expected + "\n", encoding="utf-8")
        tasks.append(
            TaskConfig(
                id=task_id,
                prompt=f"Create result.txt containing {expected}",
                verify=VerifyConfig(path=verifier, command=command),
            )
        )
    config = AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="codex"),
        tasks=tasks,
    )
    return config, starter, docs


def _factory(*agents: FakeAgent) -> ScriptedFactory:
    return ScriptedFactory(list(agents))


def test_suite_runs_every_task_in_order(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(
        FakeAgent("ONE\n"),
        FakeAgent("TWO\n"),
        FakeAgent("THREE\n"),
    )

    result = run_suite(config, agent_factory=factory)

    assert result.total == 3
    assert [item.task_id for item in result.results] == [
        "task_one",
        "task_two",
        "task_three",
    ]
    assert all(isinstance(item, TaskRunResult) for item in result.results)


def test_all_tasks_pass(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)

    result = run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("ONE\n"),
            FakeAgent("TWO\n"),
            FakeAgent("THREE\n"),
        ),
    )

    assert result.total == 3
    assert result.passed_count == 3
    assert result.failed_count == 0
    assert result.passed is True
    assert result.duration_seconds >= 0


def test_one_task_failure_does_not_stop_later_tasks(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(
        FakeAgent("ONE\n"),
        FakeAgent("WRONG\n"),
        FakeAgent("THREE\n"),
    )

    result = run_suite(config, agent_factory=factory)

    assert result.total == 3
    assert result.passed_count == 2
    assert result.failed_count == 1
    assert result.passed is False
    assert [item.passed for item in result.results] == [True, False, True]
    assert factory.calls == 3
    assert factory.agents[2].project_dir is not None


def test_multiple_tasks_fail(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)

    result = run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("BAD\n"),
            FakeAgent("TWO\n"),
            FakeAgent("BAD\n"),
        ),
    )

    assert result.passed_count == 1
    assert result.failed_count == 2
    assert result.passed is False
    assert [item.passed for item in result.results] == [False, True, False]


def test_each_task_gets_a_fresh_workspace(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(
        FakeAgent("ONE\n", leak=True),
        FakeAgent("TWO\n", reject_leak=True),
        FakeAgent("THREE\n", reject_leak=True),
    )

    result = run_suite(config, agent_factory=factory)

    paths = [agent.project_dir for agent in factory.agents]
    assert all(path is not None for path in paths)
    assert len(set(paths)) == 3
    assert factory.agents[1].saw_leak is False
    assert factory.agents[2].saw_leak is False
    assert result.passed is True


def test_original_starter_and_docs_stay_unchanged(tmp_path: Path) -> None:
    config, starter, docs = _suite(tmp_path)
    starter_before = (starter / "app.py").read_text(encoding="utf-8")
    docs_before = (docs / "users.md").read_text(encoding="utf-8")

    run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("ONE\n", leak=True),
            FakeAgent("TWO\n"),
            FakeAgent("THREE\n"),
        ),
    )

    assert (starter / "app.py").read_text(encoding="utf-8") == starter_before
    assert not (starter / "result.txt").exists()
    assert not (starter / "leaked.txt").exists()
    assert (docs / "users.md").read_text(encoding="utf-8") == docs_before


def test_agent_factory_is_called_once_per_task(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(
        FakeAgent("ONE\n"),
        FakeAgent("TWO\n"),
        FakeAgent("THREE\n"),
    )

    run_suite(config, agent_factory=factory)

    assert factory.calls == 3
    assert len({id(agent) for agent in factory.agents}) == 3


def test_default_path_delegates_agent_creation_to_run_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _starter, _docs = _suite(tmp_path)
    seen: list[tuple[str, object]] = []

    def fake_run_task(
        _config: AgentDocsConfig,
        task: TaskConfig,
        *,
        agent: object = None,
        verifier_timeout_seconds: float = 60,
        on_progress: object = None,
        runtime: object = None,
    ) -> TaskRunResult:
        del runtime
        seen.append((task.id, agent))
        return TaskRunResult(
            task_id=task.id,
            agent_result=AgentRunResult(
                agent="codex",
                exit_code=0,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=0.0,
            ),
            verifier_result=VerifierResult(
                command=("check",),
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            ),
            duration_seconds=0.0,
        )

    monkeypatch.setattr("agentdocs.suite.run_task", fake_run_task)

    result = run_suite(config, verifier_timeout_seconds=15)

    assert [item[0] for item in seen] == ["task_one", "task_two", "task_three"]
    assert all(agent is None for _task_id, agent in seen)
    assert result.passed is True
    assert result.total == 3


def test_suite_passed_follows_task_results_not_agent_exit_codes(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)

    result = run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("ONE\n", exit_code=0),
            FakeAgent("WRONG\n", exit_code=0),
            FakeAgent("THREE\n", exit_code=1),
        ),
    )

    assert [item.agent_completed_cleanly for item in result.results] == [
        True,
        True,
        False,
    ]
    assert [item.passed for item in result.results] == [True, False, True]
    assert result.total == 3
    assert result.passed_count == 2
    assert result.failed_count == 1
    assert result.passed is False


def test_infrastructure_error_on_first_task_propagates(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(
        FakeAgent("ONE\n", error=AgentError("agent exploded")),
        FakeAgent("TWO\n"),
        FakeAgent("THREE\n"),
    )

    with pytest.raises(AgentError, match="agent exploded"):
        run_suite(config, agent_factory=factory)

    assert factory.calls == 1


def test_infrastructure_error_on_later_task_propagates_after_earlier_tasks(
    tmp_path: Path,
) -> None:
    config, _starter, _docs = _suite(tmp_path)
    tasks = list(config.tasks)
    tasks[1] = tasks[1].model_copy(
        update={
            "verify": tasks[1].verify.model_copy(
                update={"command": "imaginary-test-runner check.py"}
            )
        }
    )
    broken = config.model_copy(update={"tasks": tasks})
    factory = _factory(
        FakeAgent("ONE\n"),
        FakeAgent("TWO\n"),
        FakeAgent("THREE\n"),
    )

    with pytest.raises(VerifierExecutableNotFoundError, match="imaginary-test-runner"):
        run_suite(broken, agent_factory=factory)

    assert factory.calls == 2
    assert factory.agents[0].project_dir is not None
    assert not factory.agents[0].project_dir.exists()


def test_invalid_verifier_timeout_raises_before_tasks(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    factory = _factory(FakeAgent("ONE\n"))

    with pytest.raises(ValueError, match="verifier_timeout_seconds must be greater than 0"):
        run_suite(config, agent_factory=factory, verifier_timeout_seconds=0)

    assert factory.calls == 0


def test_suite_result_type(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)

    result = run_suite(
        config,
        agent_factory=_factory(FakeAgent("ONE\n"), FakeAgent("TWO\n"), FakeAgent("THREE\n")),
    )

    assert isinstance(result, SuiteRunResult)
    assert result.passed_count + result.failed_count == result.total
    assert result.execution_complete is True
    assert result.blocking_agent_failure_count == 0
    assert result.unknown_agent_failure_count == 0


def test_blocking_provider_failure_keeps_the_suite_running(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    quota = AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.quota_or_credit,
        blocking=True,
        rule_id="claude.credit_balance_low",
        source="stderr",
    )

    result = run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("NO\n", exit_code=1, failure=quota),
            FakeAgent("TWO\n"),
            FakeAgent("THREE\n"),
        ),
    )

    assert result.passed is False
    assert result.execution_complete is False
    assert result.blocking_agent_failure_count == 1
    assert result.unknown_agent_failure_count == 0
    assert [item.passed for item in result.results] == [False, True, True]
    assert result.blocking_failure_counts()["quota_or_credit"] == 1


def test_unknown_agent_failure_leaves_execution_complete(tmp_path: Path) -> None:
    config, _starter, _docs = _suite(tmp_path)
    unknown = AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.unknown_agent_failure,
        blocking=False,
        rule_id="unmatched_nonzero_exit",
        source="exit_code",
    )

    result = run_suite(
        config,
        agent_factory=_factory(
            FakeAgent("ONE\n", exit_code=1, failure=unknown),
            FakeAgent("TWO\n"),
            FakeAgent("THREE\n"),
        ),
    )

    assert result.passed is True
    assert result.execution_complete is True
    assert result.blocking_agent_failure_count == 0
    assert result.unknown_agent_failure_count == 1
