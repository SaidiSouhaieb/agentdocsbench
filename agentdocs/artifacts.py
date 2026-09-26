"""Persist a completed suite as JSON, Markdown, and raw process logs."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentdocs.config import AgentDocsConfig
from agentdocs.fingerprint import (
    BenchmarkFingerprint,
    compute_benchmark_fingerprint,
    fingerprint_manifest_document,
    fingerprint_summary,
)
from agentdocs.suite import SuiteRunResult, TaskRunResult
from agentdocs.agents.failures import failure_payload
from agentdocs.execution.runtime_info import runtime_document
from agentdocs.markdown_text import markdown_table_cell
from agentdocs.workspace_changes import changes_document, file_change_label

_SCHEMA_VERSION = 6
_SAFE_TASK_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_RUN_ID_ATTEMPTS = 8
_TASK_NAME_LIMIT = 80


class ArtifactError(RuntimeError):
    """Raised when a completed suite cannot be written to disk."""


@dataclass(frozen=True)
class RunArtifacts:
    """Locations of one persisted benchmark run."""

    run_id: str
    root: Path
    result_json: Path
    summary_markdown: Path
    benchmark_json: Path


def write_run_artifacts(
    config: AgentDocsConfig,
    result: SuiteRunResult,
    *,
    output_root: Path,
    benchmark_fingerprint: BenchmarkFingerprint | None = None,
) -> RunArtifacts:
    """Write one self-contained run directory for a finished suite.

    The suite is not executed here. ``output_root`` is the directory that
    contains run directories. A failed write removes its staging directory
    and leaves any earlier run untouched.

    Pass ``benchmark_fingerprint`` when it was computed before the suite ran.
    When it is omitted, the fingerprint is computed from ``config`` at write
    time for programmatic callers.
    """
    fingerprint = benchmark_fingerprint or compute_benchmark_fingerprint(config)
    root = Path(output_root)
    _ensure_output_root(root)
    run_id = _allocate_run_id(root)
    created_at = datetime.now(timezone.utc)
    staging = root / f".run-{run_id}.tmp"
    destination = root / run_id
    try:
        staging.mkdir()
        _write_run_directory(staging, config, result, run_id, created_at, fingerprint)
        if destination.exists():
            raise ArtifactError(f"Run directory already exists: {destination}")
        os.rename(staging, destination)
    except ArtifactError:
        _remove_staging(staging)
        raise
    except OSError as exc:
        _remove_staging(staging)
        raise ArtifactError(f"Could not write run artifacts: {exc}") from exc
    return RunArtifacts(
        run_id=run_id,
        root=destination,
        result_json=destination / "result.json",
        summary_markdown=destination / "summary.md",
        benchmark_json=destination / "benchmark.json",
    )


def _ensure_output_root(output_root: Path) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise ArtifactError(f"Artifact output root is not a directory: {output_root}")
    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactError(
            f"Could not create artifact output root {output_root}: {exc}"
        ) from exc


def _allocate_run_id(output_root: Path) -> str:
    for _ in range(_RUN_ID_ATTEMPTS):
        run_id = _new_run_id()
        if (output_root / run_id).exists():
            continue
        if (output_root / f".run-{run_id}.tmp").exists():
            continue
        return run_id
    raise ArtifactError("Could not allocate a unique run id without replacing a run.")


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{timestamp}-{suffix}"


def _write_run_directory(
    run_dir: Path,
    config: AgentDocsConfig,
    result: SuiteRunResult,
    run_id: str,
    created_at: datetime,
    fingerprint: BenchmarkFingerprint,
) -> None:
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir()
    task_payloads: list[dict[str, object]] = []
    for index, task in enumerate(result.results, start=1):
        folder = tasks_dir / _task_folder_name(index, task.task_id)
        _require_inside(tasks_dir, folder)
        folder.mkdir()
        logs = _write_task_logs(folder, task)
        task_payloads.append(_task_payload(run_dir, task, logs))
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent_type": config.agent.type,
        "requested_model": config.agent.model,
        "benchmark": fingerprint_summary(fingerprint),
        "summary": {
            "total": result.total,
            "passed": result.passed_count,
            "failed": result.failed_count,
            "suite_passed": result.passed,
            "duration_seconds": result.duration_seconds,
        },
        "execution": {
            "complete": result.execution_complete,
            "blocking_agent_failures": result.blocking_agent_failure_count,
            "unknown_agent_failures": result.unknown_agent_failure_count,
        },
        "runtime": runtime_document(result.runtime),
        "tasks": task_payloads,
    }
    _write_text(
        run_dir / "result.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    _write_text(
        run_dir / "benchmark.json",
        json.dumps(fingerprint_manifest_document(fingerprint), indent=2, ensure_ascii=False) + "\n",
    )
    _write_text(run_dir / "summary.md", _summary_markdown(config, result, run_id, fingerprint))


def _write_task_logs(folder: Path, task: TaskRunResult) -> dict[str, Path]:
    logs = {
        "stdout": folder / "agent.stdout.log",
        "stderr": folder / "agent.stderr.log",
        "verifier_stdout": folder / "verifier.stdout.log",
        "verifier_stderr": folder / "verifier.stderr.log",
        "changes": folder / "changes.json",
    }
    _write_text(logs["stdout"], task.agent_result.stdout)
    _write_text(logs["stderr"], task.agent_result.stderr)
    _write_text(logs["verifier_stdout"], task.verifier_result.stdout)
    _write_text(logs["verifier_stderr"], task.verifier_result.stderr)
    _write_text(
        logs["changes"],
        json.dumps(changes_document(task.workspace_changes), indent=2, ensure_ascii=False) + "\n",
    )
    return logs


def _task_payload(
    run_dir: Path,
    task: TaskRunResult,
    logs: dict[str, Path],
) -> dict[str, object]:
    return {
        "id": task.task_id,
        "passed": task.passed,
        "duration_seconds": task.duration_seconds,
        "agent": {
            "name": task.agent_result.agent,
            "requested_model": task.agent_result.requested_model,
            "resolved_model": task.agent_result.resolved_model,
            "exit_code": task.agent_result.exit_code,
            "completed_cleanly": task.agent_completed_cleanly,
            "duration_seconds": task.agent_result.duration_seconds,
            "event_count": len(task.agent_result.events),
            "stdout_file": _relative_posix(run_dir, logs["stdout"]),
            "stderr_file": _relative_posix(run_dir, logs["stderr"]),
            "failure": failure_payload(task.agent_result.failure),
        },
        "verifier": {
            "command": list(task.verifier_result.command),
            "exit_code": task.verifier_result.exit_code,
            "passed": task.verifier_result.passed,
            "duration_seconds": task.verifier_result.duration_seconds,
            "stdout_file": _relative_posix(run_dir, logs["verifier_stdout"]),
            "stderr_file": _relative_posix(run_dir, logs["verifier_stderr"]),
        },
        "changes": _changes_summary(run_dir, task, logs["changes"]),
    }


def _changes_summary(run_dir: Path, task: TaskRunResult, path: Path) -> dict[str, object]:
    changes = task.workspace_changes
    return {
        "manifest_file": _relative_posix(run_dir, path),
        "before_sha256": changes.before_sha256,
        "after_sha256": changes.after_sha256,
        "files_added": len(changes.added_files),
        "files_modified": len(changes.modified_files),
        "files_deleted": len(changes.deleted_files),
        "directories_added": len(changes.added_directories),
        "directories_deleted": len(changes.deleted_directories),
        "type_changed": len(changes.type_changes),
    }


def _summary_markdown(
    config: AgentDocsConfig,
    result: SuiteRunResult,
    run_id: str,
    fingerprint: BenchmarkFingerprint,
) -> str:
    lines = [
        "# AgentDocsBench Run",
        "",
        f"Run ID: `{run_id}`",
        "",
        f"Agent: `{config.agent.type}`",
        "",
        _requested_model_line(config.agent.model),
        "",
        _resolved_model_line(result),
        "",
        f"Benchmark fingerprint: `sha256:{fingerprint.overall_sha256}`",
        "",
        f"Fingerprint schema: {fingerprint.schema_version}",
        "",
        _runtime_markdown(result),
        "",
        "## Summary",
        "",
        f"- Total: {result.total}",
        f"- Passed: {result.passed_count}",
        f"- Failed: {result.failed_count}",
        f"- Suite passed: {'yes' if result.passed else 'no'}",
        f"- Duration: {result.duration_seconds:.2f}s",
        "",
        "## Tasks",
        "",
        "| Task | Result | Agent Exit | Verifier Exit | Changes | Duration |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for task in result.results:
        label = "PASS" if task.passed else "FAIL"
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(task.task_id),
                    label,
                    str(task.agent_result.exit_code),
                    str(task.verifier_result.exit_code),
                    file_change_label(task.workspace_changes),
                    f"{task.duration_seconds:.2f}s",
                ]
            )
            + " |"
        )
    lines.extend(["", "+ added, ~ modified, - deleted", ""])
    lines.extend(_execution_markdown(result))
    return "\n".join(lines)


def _runtime_markdown(result: SuiteRunResult) -> str:
    info = result.runtime
    if info.backend != "docker" or info.agent is None or info.verifier is None:
        return f"Runtime: `{info.backend}`"
    return (
        f"Runtime: `docker`\n\n"
        f"Agent image: `{info.agent.requested_image}`\n\n"
        f"Verifier image: `{info.verifier.requested_image}`"
    )


def _execution_markdown(result: SuiteRunResult) -> list[str]:
    status = "COMPLETE" if result.execution_complete else "INCOMPLETE"
    lines = [
        "## Provider execution",
        "",
        f"Status: {status}",
        f"Blocking failures: {result.blocking_agent_failure_count}",
        f"Unknown agent failures: {result.unknown_agent_failure_count}",
        "",
    ]
    recorded = [
        task
        for task in result.results
        if task.agent_result.failure is not None
    ]
    if recorded:
        lines.extend(
            [
                "| Task | Agent failure | Blocking |",
                "| --- | --- | --- |",
            ]
        )
        for task in recorded:
            failure = task.agent_result.failure
            assert failure is not None
            blocking = "yes" if failure.blocking else "no"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(task.task_id),
                        failure.kind.value,
                        blocking,
                    ]
                )
                + " |"
            )
        lines.append("")
    return lines


def _requested_model_line(model: str | None) -> str:
    if model is None:
        return "Requested model: provider default"
    return f"Requested model: `{_markdown_cell(model)}`"


def _resolved_model_line(result: SuiteRunResult) -> str:
    resolved = {task.agent_result.resolved_model for task in result.results}
    if len(resolved) == 1:
        model = next(iter(resolved))
        if model is not None:
            return f"Resolved model: `{_markdown_cell(model)}`"
    return "Resolved model: not reported"


def _task_folder_name(index: int, task_id: str) -> str:
    sanitized = _SAFE_TASK_CHARS.sub("_", task_id).strip("._")
    if not sanitized or sanitized in {".", ".."}:
        sanitized = "task"
    if len(sanitized) > _TASK_NAME_LIMIT:
        sanitized = sanitized[:_TASK_NAME_LIMIT].strip("._") or "task"
    name = f"{index:03d}-{sanitized}"
    if name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
        return f"{index:03d}-task"
    return name


def _require_inside(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise ArtifactError(f"Task directory escapes the run tasks directory: {child}")


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _markdown_cell(value: str) -> str:
    return markdown_table_cell(value)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _remove_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
