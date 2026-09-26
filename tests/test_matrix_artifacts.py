from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdocs import AgentRunResult, ArtifactError, TaskRunResult, VerifierResult
from agentdocs.fingerprint import BenchmarkFingerprint, FileManifestEntry, TreeManifest
from agentdocs.matrix import MatrixRunResult, MatrixTargetResult
from agentdocs.matrix_artifacts import write_matrix_artifacts
from agentdocs.suite import SuiteRunResult


def _fingerprint() -> BenchmarkFingerprint:
    docs = TreeManifest(
        sha256="a" * 64,
        directories=(),
        files=(
            FileManifestEntry(
                relative_path="secret-docs-name.md",
                sha256="e" * 64,
                size_bytes=4,
            ),
        ),
    )
    return BenchmarkFingerprint(
        schema_version=1,
        algorithm="sha256",
        overall_sha256="b" * 64,
        docs=docs,
        starter=docs,
        tasks_sha256="c" * 64,
        tasks=(),
        verifiers_sha256="d" * 64,
        verifiers=(),
    )


def _suite(task_id: str, *, passed: bool) -> SuiteRunResult:
    return SuiteRunResult(
        results=(
            TaskRunResult(
                task_id=task_id,
                agent_result=AgentRunResult(
                    agent="cursor",
                    exit_code=0,
                    events=(),
                    stdout="secret-stdout",
                    stderr="",
                    duration_seconds=1.0,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0 if passed else 1,
                    stdout="",
                    stderr="secret-stderr",
                    duration_seconds=0.1,
                ),
                duration_seconds=1.0,
            ),
        ),
        duration_seconds=2.5,
    )


def _result() -> MatrixRunResult:
    completed = MatrixTargetResult(
        target_id="cursor-default",
        agent_type="cursor",
        requested_model=None,
        suite_result=_suite("create_user", passed=True),
        error_type=None,
        error_message=None,
        duration_seconds=2.5,
    )
    failed = MatrixTargetResult(
        target_id="claude-named",
        agent_type="claude",
        requested_model="model-b",
        suite_result=None,
        error_type="AgentTimeoutError",
        error_message="too slow",
        duration_seconds=0.4,
    )
    return MatrixRunResult(targets=(completed, failed), duration_seconds=3.0)


def test_matrix_artifact_records_completed_and_error_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTDOCS_SHOULD_NOT_LEAK", "leak-marker-xyz")

    artifacts = write_matrix_artifacts(
        _result(),
        task_ids=("create_user",),
        run_ids={"cursor-default": "20260926T130000Z-aaaaaaaa"},
        output_root=tmp_path,
        benchmark_fingerprint=_fingerprint(),
    )
    payload = json.loads(artifacts.matrix_json.read_text(encoding="utf-8"))
    summary = artifacts.summary_markdown.read_text(encoding="utf-8")
    text = artifacts.matrix_json.read_text(encoding="utf-8")

    assert payload["schema_version"] == 4
    assert payload["benchmark"]["overall_sha256"] == "b" * 64
    assert payload["benchmark"]["manifest_path"] == "benchmark.json"
    assert "files" not in payload["benchmark"]
    assert "secret-docs-name.md" not in text
    assert "secret-docs-name.md" in artifacts.benchmark_json.read_text(encoding="utf-8")
    manifest = json.loads(artifacts.benchmark_json.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["overall_sha256"] == "b" * 64
    assert payload["matrix_run_id"] == artifacts.matrix_run_id
    assert "/" not in artifacts.matrix_run_id
    assert payload["summary"]["completed_targets"] == 1
    assert payload["summary"]["error_targets"] == 1
    completed = payload["targets"][0]
    error = payload["targets"][1]
    assert completed["status"] == "pass"
    assert completed["runtime"]["backend"] == "local"
    assert error["runtime"] == {"schema_version": 1, "backend": "local"}
    assert completed["run_id"] == "20260926T130000Z-aaaaaaaa"
    assert completed["passed"] == 1
    assert completed["tasks"] == [{"id": "create_user", "passed": True}]
    assert error["status"] == "error"
    assert error["error_type"] == "AgentTimeoutError"
    assert "run_id" not in error
    assert "secret-stdout" not in text
    assert "secret-stderr" not in text
    assert "leak-marker-xyz" not in text
    assert "PASS" in summary
    assert "ERROR" in summary
    assert "winner" not in summary.lower()
    assert "20260926T130000Z-aaaaaaaa" in summary
    assert "Benchmark fingerprint: `sha256:" + ("b" * 64) + "`" in summary


def test_failed_matrix_write_removes_staging_and_keeps_existing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = tmp_path / "already-there"
    kept.mkdir()
    (kept / "keep.txt").write_text("keep", encoding="utf-8")

    def fail_rename(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("agentdocs.matrix_artifacts.os.rename", fail_rename)
    with pytest.raises(ArtifactError, match="Could not write matrix artifacts"):
        write_matrix_artifacts(
            _result(),
            task_ids=("create_user",),
            run_ids={},
            output_root=tmp_path,
            benchmark_fingerprint=_fingerprint(),
        )

    assert (kept / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob(".matrix-*.tmp")) == []


def test_existing_matrix_directory_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "fixedid"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr("agentdocs.matrix_artifacts._new_matrix_run_id", lambda: "fixedid")

    with pytest.raises(ArtifactError, match="unique matrix run id"):
        write_matrix_artifacts(
            _result(),
            task_ids=("create_user",),
            run_ids={},
            output_root=tmp_path,
            benchmark_fingerprint=_fingerprint(),
        )

    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"
