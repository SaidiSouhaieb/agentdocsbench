from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs import AgentRunResult, AgentTimeoutError, TaskRunResult, VerifierResult
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.matrix import run_matrix, target_status
from agentdocs.matrix_config import MatrixConfig, MatrixTargetConfig
from agentdocs.matrix_progress import MatrixFinished, MatrixTargetStarted
from agentdocs.suite import SuiteRunResult

def _benchmark() -> AgentDocsConfig:
    task = TaskConfig(
        id="create_user",
        prompt="Create a user named Alice.",
        verify=VerifyConfig(path=Path("/verifier"), command="python3 check.py"),
    )
    return AgentDocsConfig.model_construct(
        version=1,
        docs=Path("/docs"),
        starter=Path("/starter"),
        agent=AgentConfig(type="cursor", model="original"),
        tasks=[task, task.model_copy(update={"id": "create_project"})],
    )


def _matrix(*targets: tuple[str, str, str | None]) -> MatrixConfig:
    return MatrixConfig(
        version=1,
        targets=[
            MatrixTargetConfig(id=target_id, agent=agent, model=model)
            for target_id, agent, model in targets
        ],
    )


def _suite(*, passed: bool) -> SuiteRunResult:
    return SuiteRunResult(
        results=(
            TaskRunResult(
                task_id="create_user",
                agent_result=AgentRunResult(
                    agent="cursor",
                    exit_code=0,
                    events=(),
                    stdout="",
                    stderr="",
                    duration_seconds=1.0,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0 if passed else 1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.1,
                ),
                duration_seconds=1.1,
            ),
        ),
        duration_seconds=1.1,
    )


def test_targets_run_in_order_without_mutating_the_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = _benchmark()
    seen: list[object] = []

    def fake_run_suite(config: AgentDocsConfig, **kwargs: object) -> SuiteRunResult:
        seen.append(config)
        assert kwargs["on_progress"] is sentinel
        return _suite(passed=True)

    sentinel = object()
    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    matrix = _matrix(
        ("cursor-default", "cursor", None),
        ("codex-named", "codex", "model-a"),
    )

    result = run_matrix(benchmark, matrix, on_progress=sentinel)  # type: ignore[arg-type]

    assert [target.target_id for target in result.targets] == [
        "cursor-default",
        "codex-named",
    ]
    assert len(seen) == 2
    first = seen[0]
    second = seen[1]
    assert isinstance(first, AgentDocsConfig)
    assert isinstance(second, AgentDocsConfig)
    assert first is not benchmark
    assert first.agent.type == "cursor"
    assert first.agent.model is None
    assert second.agent.type == "codex"
    assert second.agent.model == "model-a"
    assert [task.id for task in first.tasks] == ["create_user", "create_project"]
    assert benchmark.agent.type == "cursor"
    assert benchmark.agent.model == "original"
    assert result.duration_seconds >= 0
    assert all(target.duration_seconds >= 0 for target in result.targets)


def test_benchmark_failure_does_not_stop_later_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_run_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        calls["n"] += 1
        return _suite(passed=calls["n"] == 1)

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    result = run_matrix(
        _benchmark(),
        _matrix(("first", "cursor", None), ("second", "codex", "model-a")),
    )

    assert calls["n"] == 2
    assert result.targets[0].completed is True
    assert result.targets[0].suite_result is not None
    assert result.targets[0].suite_result.passed is True
    assert result.targets[1].completed is True
    assert result.targets[1].suite_result is not None
    assert result.targets[1].suite_result.passed is False
    assert result.error_count == 0


def test_infrastructure_error_is_recorded_and_later_target_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_run_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        calls["n"] += 1
        if calls["n"] == 2:
            raise AgentTimeoutError("too slow")
        return _suite(passed=True)

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    result = run_matrix(
        _benchmark(),
        _matrix(
            ("first", "cursor", None),
            ("middle", "claude", "model-b"),
            ("last", "codex", None),
        ),
    )

    assert calls["n"] == 3
    assert [target.completed for target in result.targets] == [True, False, True]
    failed = result.targets[1]
    assert failed.error_type == "AgentTimeoutError"
    assert failed.error_message == "too slow"
    assert failed.suite_result is None
    assert result.error_count == 1


def test_unexpected_exception_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    events: list[object] = []

    def fake_run_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        calls["n"] += 1
        raise RuntimeError("programming bug")

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    with pytest.raises(RuntimeError, match="programming bug"):
        run_matrix(
            _benchmark(),
            _matrix(("first", "cursor", None), ("second", "codex", None)),
            on_matrix_progress=events.append,
        )

    assert calls["n"] == 1
    assert not any(isinstance(event, MatrixFinished) for event in events)


def test_duplicate_agent_model_pairs_run_when_ids_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_run_suite(config: AgentDocsConfig, **_kwargs: object) -> SuiteRunResult:
        seen.append(config.agent.model or "")
        return _suite(passed=True)

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    result = run_matrix(
        _benchmark(),
        _matrix(("run-a", "cursor", "model-a"), ("run-b", "cursor", "model-a")),
    )

    assert seen == ["model-a", "model-a"]
    assert [target.target_id for target in result.targets] == ["run-a", "run-b"]


def test_provider_error_does_not_stop_later_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run_suite(config: AgentDocsConfig, **_kwargs: object) -> SuiteRunResult:
        calls.append(config.agent.type)
        blocking = config.agent.type == "claude"
        failure = None
        if blocking:
            failure = AgentFailureClassification(
                schema_version=FAILURE_SCHEMA_VERSION,
                kind=AgentFailureKind.quota_or_credit,
                blocking=True,
                rule_id="claude.credit_balance_low",
                source="stderr",
            )
        return SuiteRunResult(
            results=(
                TaskRunResult(
                    task_id="create_user",
                    agent_result=AgentRunResult(
                        agent=config.agent.type,
                        exit_code=1 if blocking else 0,
                        events=(),
                        stdout="",
                        stderr="",
                        duration_seconds=1.0,
                        failure=failure,
                    ),
                    verifier_result=VerifierResult(
                        command=("python3", "check.py"),
                        exit_code=1 if blocking else 0,
                        stdout="",
                        stderr="",
                        duration_seconds=0.1,
                    ),
                    duration_seconds=1.1,
                ),
            ),
            duration_seconds=1.1,
        )

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    result = run_matrix(
        _benchmark(),
        _matrix(("claude-default", "claude", None), ("cursor-default", "cursor", None)),
    )

    assert calls == ["claude", "cursor"]
    assert [target_status(target) for target in result.targets] == ["provider_error", "pass"]
    assert result.targets[0].suite_result is not None
    assert result.targets[0].suite_result.execution_complete is False
    assert result.targets[1].suite_result is not None
    assert result.targets[1].suite_result.passed is True


def test_unknown_agent_failure_is_not_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        return SuiteRunResult(
            results=(
                TaskRunResult(
                    task_id="create_user",
                    agent_result=AgentRunResult(
                        agent="cursor",
                        exit_code=1,
                        events=(),
                        stdout="",
                        stderr="",
                        duration_seconds=1.0,
                        failure=AgentFailureClassification(
                            schema_version=FAILURE_SCHEMA_VERSION,
                            kind=AgentFailureKind.unknown_agent_failure,
                            blocking=False,
                            rule_id="unmatched_nonzero_exit",
                            source="exit_code",
                        ),
                    ),
                    verifier_result=VerifierResult(
                        command=("python3", "check.py"),
                        exit_code=1,
                        stdout="",
                        stderr="",
                        duration_seconds=0.1,
                    ),
                    duration_seconds=1.1,
                ),
            ),
            duration_seconds=1.1,
        )

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    result = run_matrix(_benchmark(), _matrix(("cursor-default", "cursor", None)))

    assert target_status(result.targets[0]) == "fail"
    assert result.targets[0].suite_result is not None
    assert result.targets[0].suite_result.execution_complete is True


def test_matrix_progress_wraps_each_target(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []

    def fake_run_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        return _suite(passed=True)

    monkeypatch.setattr("agentdocs.matrix.run_suite", fake_run_suite)
    run_matrix(
        _benchmark(),
        _matrix(("only", "claude", None)),
        on_matrix_progress=events.append,
    )

    assert [type(event).__name__ for event in events] == [
        "MatrixStarted",
        "MatrixTargetStarted",
        "MatrixTargetFinished",
        "MatrixFinished",
    ]
    started = events[1]
    assert isinstance(started, MatrixTargetStarted)
    assert started.target_id == "only"
    assert started.index == 1
    assert started.agent == "claude"
    assert started.requested_model is None
