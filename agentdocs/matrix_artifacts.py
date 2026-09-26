"""Persist a matrix comparison without copying child-run logs."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentdocs.artifacts import ArtifactError
from agentdocs.fingerprint import (
    BenchmarkFingerprint,
    fingerprint_manifest_document,
    fingerprint_summary,
)
from agentdocs.execution.runtime_info import runtime_document
from agentdocs.markdown_text import markdown_table_cell
from agentdocs.matrix import MatrixRunResult, MatrixTargetResult, target_status, target_status_label

_SCHEMA_VERSION = 4
_RUN_ID_ATTEMPTS = 8


@dataclass(frozen=True)
class MatrixArtifacts:
    """Locations of one persisted matrix report."""

    matrix_run_id: str
    root: Path
    matrix_json: Path
    summary_markdown: Path
    benchmark_json: Path


def write_matrix_artifacts(
    result: MatrixRunResult,
    *,
    task_ids: tuple[str, ...],
    run_ids: dict[str, str],
    output_root: Path,
    benchmark_fingerprint: BenchmarkFingerprint,
) -> MatrixArtifacts:
    """Write the matrix report for a finished matrix.

    ``run_ids`` maps a completed target id to its normal run id. Error targets
    are omitted. Raw agent logs are not copied. ``benchmark_fingerprint`` is
    the one identity computed for the benchmark before any target ran.
    """
    root = Path(output_root)
    _ensure_output_root(root)
    matrix_run_id = _allocate_matrix_run_id(root)
    created_at = datetime.now(timezone.utc)
    staging = root / f".matrix-{matrix_run_id}.tmp"
    destination = root / matrix_run_id
    try:
        staging.mkdir()
        _write_matrix_directory(
            staging,
            result,
            task_ids,
            run_ids,
            matrix_run_id,
            created_at,
            benchmark_fingerprint,
        )
        if destination.exists():
            raise ArtifactError(f"Matrix directory already exists: {destination}")
        os.rename(staging, destination)
    except ArtifactError:
        _remove_staging(staging)
        raise
    except OSError as exc:
        _remove_staging(staging)
        raise ArtifactError(f"Could not write matrix artifacts: {exc}") from exc
    return MatrixArtifacts(
        matrix_run_id=matrix_run_id,
        root=destination,
        matrix_json=destination / "matrix.json",
        summary_markdown=destination / "summary.md",
        benchmark_json=destination / "benchmark.json",
    )


def _write_matrix_directory(
    directory: Path,
    result: MatrixRunResult,
    task_ids: tuple[str, ...],
    run_ids: dict[str, str],
    matrix_run_id: str,
    created_at: datetime,
    fingerprint: BenchmarkFingerprint,
) -> None:
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "matrix_run_id": matrix_run_id,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "benchmark": fingerprint_summary(fingerprint),
        "task_ids": list(task_ids),
        "summary": {
            "target_count": len(result.targets),
            "completed_targets": result.completed_count,
            "error_targets": result.error_count,
            "provider_error_targets": result.provider_error_count,
            "duration_seconds": result.duration_seconds,
        },
        "targets": [
            _target_payload(target, run_ids.get(target.target_id), result.runtime_backend)
            for target in result.targets
        ],
    }
    (directory / "matrix.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (directory / "benchmark.json").write_text(
        json.dumps(fingerprint_manifest_document(fingerprint), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (directory / "summary.md").write_text(
        _summary_markdown(result, task_ids, run_ids, matrix_run_id, fingerprint),
        encoding="utf-8",
    )


def _target_payload(
    target: MatrixTargetResult,
    run_id: str | None,
    runtime_backend: str,
) -> dict[str, object]:
    status = target_status(target)
    payload: dict[str, object] = {
        "id": target.target_id,
        "agent_type": target.agent_type,
        "requested_model": target.requested_model,
        "status": status,
        "duration_seconds": target.duration_seconds,
    }
    if target.suite_result is None:
        payload["error_type"] = target.error_type
        payload["error_message"] = target.error_message
        payload["runtime"] = {"schema_version": 1, "backend": runtime_backend}
        return payload
    suite = target.suite_result
    payload["run_id"] = run_id
    payload["total"] = suite.total
    payload["passed"] = suite.passed_count
    payload["failed"] = suite.failed_count
    payload["tasks"] = [
        {"id": task.task_id, "passed": task.passed}
        for task in suite.results
    ]
    payload["execution"] = {
        "complete": suite.execution_complete,
        "blocking_agent_failures": suite.blocking_agent_failure_count,
        "unknown_agent_failures": suite.unknown_agent_failure_count,
    }
    if status == "provider_error":
        payload["provider_failures"] = suite.blocking_failure_counts()
    payload["runtime"] = runtime_document(suite.runtime)
    return payload


def _summary_markdown(
    result: MatrixRunResult,
    task_ids: tuple[str, ...],
    run_ids: dict[str, str],
    matrix_run_id: str,
    fingerprint: BenchmarkFingerprint,
) -> str:
    lines = [
        "# AgentDocsBench Matrix Run",
        "",
        f"Matrix ID: `{matrix_run_id}`",
        "",
        f"Benchmark fingerprint: `sha256:{fingerprint.overall_sha256}`",
        "",
        f"Fingerprint schema: {fingerprint.schema_version}",
        "",
        f"Targets: {len(result.targets)}",
        "",
        f"Tasks: {len(task_ids)}",
        "",
        "## Targets",
        "",
        "| Target | Agent | Requested model | Status | Passed | Failed | Duration | Run ID |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for target in result.targets:
        model = "provider default" if target.requested_model is None else target.requested_model
        if target.suite_result is None:
            passed = "ERROR"
            failed = "ERROR"
            run_id = ""
        else:
            passed = str(target.suite_result.passed_count)
            failed = str(target.suite_result.failed_count)
            run_id = run_ids.get(target.target_id, "")
        status = target_status_label(target)
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(target.target_id),
                    _cell(target.agent_type),
                    _cell(model),
                    status,
                    passed,
                    failed,
                    f"{target.duration_seconds:.2f}s",
                    _cell(run_id),
                ]
            )
            + " |"
        )
    for target in result.targets:
        if target_status(target) != "provider_error" or target.suite_result is None:
            continue
        parts = [
            f"{kind}: {count}"
            for kind, count in target.suite_result.blocking_failure_counts().items()
            if count
        ]
        detail = ", ".join(parts) if parts else "blocking provider failure"
        lines.extend(["", f"{_cell(target.target_id)}: PROVIDER_ERROR · {detail}"])
    lines.extend(
        [
            "",
            "## Task comparison",
            "",
            "| Task | " + " | ".join(_cell(target.target_id) for target in result.targets) + " |",
            "| --- | " + " | ".join("---" for _ in result.targets) + " |",
        ]
    )
    for task_id in task_ids:
        cells = [_task_cell(target, task_id) for target in result.targets]
        lines.append("| " + " | ".join([_cell(task_id), *cells]) + " |")
    lines.append("")
    return "\n".join(lines)


def _task_cell(target: MatrixTargetResult, task_id: str) -> str:
    if target.suite_result is None:
        return "ERROR"
    for task in target.suite_result.results:
        if task.task_id == task_id:
            return "PASS" if task.passed else "FAIL"
    return "—"


def _cell(value: str) -> str:
    return markdown_table_cell(value)


def _ensure_output_root(output_root: Path) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise ArtifactError(f"Matrix output root is not a directory: {output_root}")
    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactError(
            f"Could not create matrix output root {output_root}: {exc}"
        ) from exc


def _allocate_matrix_run_id(output_root: Path) -> str:
    for _ in range(_RUN_ID_ATTEMPTS):
        matrix_run_id = _new_matrix_run_id()
        if (output_root / matrix_run_id).exists():
            continue
        if (output_root / f".matrix-{matrix_run_id}.tmp").exists():
            continue
        return matrix_run_id
    raise ArtifactError("Could not allocate a unique matrix run id without replacing a run.")


def _new_matrix_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{timestamp}-{suffix}"


def _remove_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
