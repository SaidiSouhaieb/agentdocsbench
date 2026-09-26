from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdocs.compare import (
    CompareError,
    classify_benchmark_identity,
    comparison_task_ids,
    load_comparable_run,
    task_sets_match,
)


def _task(task_id: str, *, passed: bool) -> dict[str, object]:
    return {
        "id": task_id,
        "passed": passed,
        "duration_seconds": 1.5,
        "agent": {
            "name": "cursor",
            "exit_code": 0,
            "completed_cleanly": True,
            "duration_seconds": 1.0,
            "event_count": 0,
            "stdout_file": "tasks/001/agent.stdout.log",
            "stderr_file": "tasks/001/agent.stderr.log",
        },
        "verifier": {
            "command": ["python3", "check.py"],
            "exit_code": 0 if passed else 1,
            "passed": passed,
            "duration_seconds": 0.2,
            "stdout_file": "tasks/001/verifier.stdout.log",
            "stderr_file": "tasks/001/verifier.stderr.log",
        },
    }


def _document(
    *,
    schema_version: int = 2,
    tasks: list[dict[str, object]] | None = None,
    requested_model: str | None = "model-a",
    include_model: bool = True,
) -> dict[str, object]:
    if tasks is None:
        tasks = [_task("create_user", passed=True), _task("create_project", passed=False)]
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "run_id": "20260926T130000Z-aaaaaaaa",
        "agent_type": "cursor",
        "summary": {
            "total": len(tasks),
            "passed": 1,
            "failed": 0,
            "suite_passed": False,
            "duration_seconds": 4.5,
        },
        "tasks": tasks,
    }
    if include_model:
        payload["requested_model"] = requested_model
    return payload


def _write(directory: Path, payload: dict[str, object], name: str = "result.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_schema_v2_records_the_requested_model(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())

    run = load_comparable_run(path)

    assert run.schema_version == 2
    assert run.requested_model == "model-a"
    assert run.requested_model_recorded is True
    assert run.task_ids == ("create_user", "create_project")
    assert run.passed_count == 1
    assert run.tasks[1].verifier_exit == 1
    assert run.duration_seconds == 4.5


def test_schema_v2_null_model_is_recorded_as_provider_default(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(requested_model=None))

    run = load_comparable_run(path)

    assert run.requested_model is None
    assert run.requested_model_recorded is True


def test_schema_v1_model_is_not_recorded(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(schema_version=1, include_model=False))

    run = load_comparable_run(path)

    assert run.schema_version == 1
    assert run.requested_model_recorded is False
    assert run.requested_model is None


def test_directory_input_reads_result_json(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    _write(directory, _document())

    run = load_comparable_run(directory)

    assert run.run_id == "20260926T130000Z-aaaaaaaa"


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(CompareError, match="not valid JSON"):
        load_comparable_run(path)


def test_unsupported_schema_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(schema_version=7))

    with pytest.raises(CompareError, match="Unsupported result schema"):
        load_comparable_run(path)


def test_schema_v4_is_fingerprint_capable(tmp_path: Path) -> None:
    payload = _document(schema_version=4)
    payload["benchmark"] = _benchmark_summary("a" * 64)
    path = _write(tmp_path, payload)

    run = load_comparable_run(path)

    assert run.schema_version == 4
    assert run.execution_recorded is False
    assert run.execution_summary() == "not recorded"
    assert run.benchmark_recorded is True
    assert run.benchmark_overall_sha256 == "a" * 64
    assert classify_benchmark_identity((run, run)) == "verified_match"


def test_schema_v5_records_execution_and_keeps_the_fingerprint(tmp_path: Path) -> None:
    payload = _document(schema_version=5)
    payload["benchmark"] = _benchmark_summary("a" * 64)
    payload["execution"] = {
        "complete": False,
        "blocking_agent_failures": 1,
        "unknown_agent_failures": 0,
    }
    tasks = payload["tasks"]
    assert isinstance(tasks, list)
    first = tasks[0]
    assert isinstance(first, dict)
    agent = first["agent"]
    assert isinstance(agent, dict)
    agent["failure"] = {
        "schema_version": 1,
        "kind": "quota_or_credit",
        "blocking": True,
        "rule_id": "claude.credit_balance_low",
        "source": "stderr",
    }
    second = tasks[1]
    assert isinstance(second, dict)
    second_agent = second["agent"]
    assert isinstance(second_agent, dict)
    second_agent["failure"] = None
    path = _write(tmp_path, payload)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "agent.stderr.log").write_text(
        "Credit balance is too low\n",
        encoding="utf-8",
    )

    run = load_comparable_run(path)

    assert run.schema_version == 5
    assert run.benchmark_recorded is True
    assert run.execution_recorded is True
    assert run.execution_complete is False
    assert run.execution_summary() == "incomplete · quota_or_credit ×1"
    assert run.tasks[1].failure_recorded is True
    assert run.tasks[1].failure_kind is None
    assert classify_benchmark_identity((run, run)) == "verified_match"


def test_schema_v3_records_the_benchmark_fingerprint(tmp_path: Path) -> None:
    payload = _document(schema_version=3)
    payload["benchmark"] = _benchmark_summary("a" * 64)
    path = _write(tmp_path, payload)

    run = load_comparable_run(path)

    assert run.schema_version == 3
    assert run.benchmark_recorded is True
    assert run.benchmark_overall_sha256 == "a" * 64
    assert run.benchmark_manifest_path == "benchmark.json"
    assert run.requested_model_recorded is True


def test_schema_v1_and_v2_are_unverified() -> None:
    older = _memory("a", ["create_user"])
    current = _memory("b", ["create_user"], benchmark="c" * 64, schema_version=3)

    assert classify_benchmark_identity((older, older)) == "unverified"
    assert classify_benchmark_identity((older, current)) == "unverified"
    assert classify_benchmark_identity((current, current)) == "verified_match"
    other = _memory("d", ["create_user"], benchmark="f" * 64, schema_version=3)
    assert classify_benchmark_identity((current, other)) == "different"


def test_schema_v3_without_benchmark_identity_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(schema_version=3))

    with pytest.raises(CompareError, match="benchmark identity"):
        load_comparable_run(path)


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    payload = _document()
    del payload["run_id"]
    path = _write(tmp_path, payload)

    with pytest.raises(CompareError, match="missing run_id"):
        load_comparable_run(path)


def test_duplicate_task_ids_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _document(tasks=[_task("create_user", passed=True), _task("create_user", passed=False)]),
    )

    with pytest.raises(CompareError, match="Duplicate task id"):
        load_comparable_run(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CompareError, match="not found"):
        load_comparable_run(tmp_path / "missing" / "result.json")


def test_task_set_helpers() -> None:
    left = _memory("a", ["create_user", "create_project"])
    right = _memory("b", ["create_user", "create_project"])
    other = _memory("c", ["create_project", "pagination"])

    assert task_sets_match((left, right)) is True
    assert task_sets_match((left, other)) is False
    assert comparison_task_ids((left, other)) == (
        "create_user",
        "create_project",
        "pagination",
    )


def _benchmark_summary(digest: str) -> dict[str, object]:
    return {
        "fingerprint_schema_version": 1,
        "algorithm": "sha256",
        "overall_sha256": digest,
        "docs_sha256": "b" * 64,
        "starter_sha256": "c" * 64,
        "tasks_sha256": "d" * 64,
        "verifiers_sha256": "e" * 64,
        "manifest_path": "benchmark.json",
    }


def _memory(
    run_id: str,
    task_ids: list[str],
    *,
    benchmark: str | None = None,
    schema_version: int = 2,
):
    from agentdocs.compare import ComparableRun, ComparableTask

    return ComparableRun(
        path=Path(run_id),
        run_id=run_id,
        agent_type="cursor",
        requested_model=None,
        requested_model_recorded=True,
        tasks=tuple(
            ComparableTask(
                task_id=task_id,
                passed=True,
                agent_exit=0,
                verifier_exit=0,
                duration_seconds=1.0,
            )
            for task_id in task_ids
        ),
        duration_seconds=1.0,
        schema_version=schema_version,
        benchmark_overall_sha256=benchmark,
        benchmark_manifest_path="benchmark.json" if benchmark is not None else None,
    )


def _fingerprinted(schema_version: int, *, runtime: dict[str, object] | None = None) -> dict[str, object]:
    payload = _document(schema_version=schema_version)
    payload["benchmark"] = _benchmark_summary("a" * 64)
    if schema_version >= 5:
        payload["execution"] = {
            "complete": True,
            "blocking_agent_failures": 0,
            "unknown_agent_failures": 0,
        }
        tasks = payload["tasks"]
        assert isinstance(tasks, list)
        for task in tasks:
            assert isinstance(task, dict)
            agent = task["agent"]
            assert isinstance(agent, dict)
            agent["failure"] = None
    if runtime is not None:
        payload["runtime"] = runtime
    return payload


_LOCAL_RUNTIME = {"schema_version": 1, "backend": "local"}
_DOCKER_RUNTIME = {
    "schema_version": 1,
    "backend": "docker",
    "agent": {
        "requested_image": "agentdocs-cursor:local",
        "image_id": "sha256:abc",
        "network": "bridge",
    },
    "verifier": {
        "requested_image": "agentdocs-verifier:local",
        "image_id": "sha256:def",
        "network": "none",
    },
}


def test_schema_v6_records_runtime_and_keeps_fingerprint_identity(tmp_path: Path) -> None:
    current = load_comparable_run(
        _write(tmp_path / "v6", _fingerprinted(6, runtime=_LOCAL_RUNTIME))
    )
    other = load_comparable_run(
        _write(tmp_path / "v6b", _fingerprinted(6, runtime=_DOCKER_RUNTIME))
    )
    version5 = load_comparable_run(_write(tmp_path / "v5", _fingerprinted(5)))
    version4 = load_comparable_run(_write(tmp_path / "v4", _fingerprinted(4)))
    version2 = load_comparable_run(_write(tmp_path / "v2", _document(schema_version=2)))

    assert current.schema_version == 6
    assert current.runtime_summary() == "local"
    assert current.execution_summary() == "complete"
    assert "agent image: sha256:abc" in other.runtime_summary()
    assert "verifier image: sha256:def" in other.runtime_summary()
    assert version5.runtime_summary() == "not recorded"
    assert version5.execution_recorded is True
    assert classify_benchmark_identity((current, other)) == "verified_match"
    assert classify_benchmark_identity((version5, current)) == "verified_match"
    assert classify_benchmark_identity((version4, current)) == "verified_match"
    assert classify_benchmark_identity((version2, current)) == "unverified"


def test_schema_v6_without_runtime_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _fingerprinted(6))

    with pytest.raises(CompareError, match="runtime"):
        load_comparable_run(path)


def test_compare_prints_a_runtime_difference_without_ranking(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    from agentdocs.cli_matrix import render_compare

    local = load_comparable_run(
        _write(tmp_path / "local", _fingerprinted(6, runtime=_LOCAL_RUNTIME))
    )
    docker = load_comparable_run(
        _write(tmp_path / "docker", _fingerprinted(6, runtime=_DOCKER_RUNTIME))
    )
    buffer = StringIO()
    render_compare(Console(file=buffer, force_terminal=False, width=120), (local, docker))
    text = buffer.getvalue()

    assert "VERIFIED MATCH" in text
    assert "A runtime: local" in text
    assert "agent image: sha256:abc" in text
    assert "Execution runtime differs between artifacts." in text
    assert "better" not in text.lower()
    assert "sandbox" not in text.lower()
