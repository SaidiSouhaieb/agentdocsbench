"""Read existing run artifacts for an offline comparison."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SCHEMAS = {1, 2, 3, 4, 5, 6}
_FAILURE_KINDS = {
    "authentication",
    "quota_or_credit",
    "rate_limit",
    "invalid_model",
    "provider_unavailable",
    "unknown_agent_failure",
}


class CompareError(ValueError):
    """Raised when a run artifact cannot be compared. This does not run a benchmark."""


@dataclass(frozen=True)
class ComparableTask:
    """One task row from a saved result.json."""

    task_id: str
    passed: bool
    agent_exit: int
    verifier_exit: int
    duration_seconds: float
    failure_kind: str | None = None
    failure_blocking: bool = False
    failure_recorded: bool = False


@dataclass(frozen=True)
class ComparableRun:
    """The fields needed to compare one saved run. Logs are not loaded.

    ``requested_model_recorded`` is false for schema version 1. A missing
    record is not the same as an explicit provider default.
    """

    path: Path
    run_id: str
    agent_type: str
    requested_model: str | None
    requested_model_recorded: bool
    tasks: tuple[ComparableTask, ...]
    duration_seconds: float
    schema_version: int
    benchmark_overall_sha256: str | None = None
    benchmark_manifest_path: str | None = None
    execution_recorded: bool = False
    execution_complete: bool | None = None
    runtime_recorded: bool = False
    runtime_backend: str | None = None
    runtime_agent_image: str | None = None
    runtime_agent_image_id: str | None = None
    runtime_verifier_image: str | None = None
    runtime_verifier_image_id: str | None = None

    @property
    def benchmark_recorded(self) -> bool:
        """True when this artifact carries a benchmark fingerprint."""
        return self.benchmark_overall_sha256 is not None

    @property
    def passed_count(self) -> int:
        return sum(task.passed for task in self.tasks)

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)

    def execution_summary(self) -> str:
        """Human execution line. Schemas 1–4 stay ``not recorded``."""
        if not self.execution_recorded or self.execution_complete is None:
            return "not recorded"
        if self.execution_complete:
            return "complete"
        counts: dict[str, int] = {}
        for task in self.tasks:
            if task.failure_recorded and task.failure_blocking and task.failure_kind:
                counts[task.failure_kind] = counts.get(task.failure_kind, 0) + 1
        parts = [f"{kind} ×{counts[kind]}" for kind in sorted(counts)]
        if not parts:
            return "incomplete"
        return "incomplete · " + " · ".join(parts)

    def runtime_summary(self) -> str:
        """Human runtime line. Schemas 1–5 stay ``not recorded``."""
        if not self.runtime_recorded or self.runtime_backend is None:
            return "not recorded"
        if self.runtime_backend == "local":
            return "local"
        agent = self.runtime_agent_image_id or self.runtime_agent_image or "unrecorded"
        verifier = self.runtime_verifier_image_id or self.runtime_verifier_image or "unrecorded"
        return f"docker\n  agent image: {agent}\n  verifier image: {verifier}"


def load_comparable_run(path: str | Path) -> ComparableRun:
    """Load ``result.json``, or ``<directory>/result.json`` when ``path`` is a directory."""
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "result.json"
    if not candidate.is_file():
        raise CompareError(f"Run artifact not found: {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CompareError(f"Run artifact is not valid JSON: {candidate}") from exc
    except OSError as exc:
        raise CompareError(f"Could not read run artifact {candidate}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CompareError(f"Run artifact must be a JSON object: {candidate}")
    return _parse_run(candidate, payload)


def classify_benchmark_identity(runs: tuple[ComparableRun, ...]) -> str:
    """Return ``verified_match``, ``different``, or ``unverified``.

    A missing fingerprint is unverified. It is not treated as a match or a
    difference. Schema 1 and schema 2 artifacts have no fingerprint.
    """
    if any(not run.benchmark_recorded for run in runs):
        return "unverified"
    distinct = {run.benchmark_overall_sha256 for run in runs}
    if len(distinct) == 1:
        return "verified_match"
    return "different"


def task_sets_match(runs: tuple[ComparableRun, ...]) -> bool:
    """True when every run lists the same task ids in the same order."""
    if not runs:
        return True
    first = runs[0].task_ids
    return all(run.task_ids == first for run in runs)


def comparison_task_ids(runs: tuple[ComparableRun, ...]) -> tuple[str, ...]:
    """Task ids from the first run, then ids that appear only in later runs."""
    ordered: list[str] = []
    seen: set[str] = set()
    for run in runs:
        for task_id in run.task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            ordered.append(task_id)
    return tuple(ordered)


def _parse_run(path: Path, payload: dict[str, Any]) -> ComparableRun:
    schema_version = payload.get("schema_version")
    if schema_version not in _SUPPORTED_SCHEMAS:
        raise CompareError(
            f"Unsupported result schema {schema_version!r} in {path}. "
            "Supported schemas: 1, 2, 3, 4, 5, 6."
        )
    run_id = _require_text(payload, "run_id", path)
    agent_type = _require_text(payload, "agent_type", path)
    summary = payload.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("duration_seconds"), int | float):
        raise CompareError(f"Run artifact is missing summary.duration_seconds: {path}")
    recorded = schema_version >= 2
    requested_model: str | None = None
    if recorded:
        if "requested_model" not in payload:
            raise CompareError(f"Run artifact is missing requested_model: {path}")
        raw_model = payload["requested_model"]
        if raw_model is not None and not isinstance(raw_model, str):
            raise CompareError(f"requested_model must be a string or null: {path}")
        requested_model = raw_model
    tasks_payload = payload.get("tasks")
    if not isinstance(tasks_payload, list) or not tasks_payload:
        raise CompareError(f"Run artifact must contain at least one task: {path}")
    tasks: list[ComparableTask] = []
    seen: set[str] = set()
    for item in tasks_payload:
        task = _parse_task(path, item, schema_version)
        if task.task_id in seen:
            raise CompareError(f"Duplicate task id {task.task_id!r} in {path}.")
        seen.add(task.task_id)
        tasks.append(task)
    benchmark_sha, manifest_path = _parse_benchmark(path, payload, schema_version)
    execution_recorded, execution_complete = _parse_execution(path, payload, schema_version)
    runtime_fields = _parse_runtime(path, payload, schema_version)
    return ComparableRun(
        path=path,
        run_id=run_id,
        agent_type=agent_type,
        requested_model=requested_model,
        requested_model_recorded=recorded,
        tasks=tuple(tasks),
        duration_seconds=float(summary["duration_seconds"]),
        schema_version=schema_version,
        benchmark_overall_sha256=benchmark_sha,
        benchmark_manifest_path=manifest_path,
        execution_recorded=execution_recorded,
        execution_complete=execution_complete,
        runtime_recorded=runtime_fields[0],
        runtime_backend=runtime_fields[1],
        runtime_agent_image=runtime_fields[2],
        runtime_agent_image_id=runtime_fields[3],
        runtime_verifier_image=runtime_fields[4],
        runtime_verifier_image_id=runtime_fields[5],
    )


def _parse_benchmark(
    path: Path,
    payload: dict[str, Any],
    schema_version: int,
) -> tuple[str | None, str | None]:
    if schema_version < 3:
        return None, None
    benchmark = payload.get("benchmark")
    if not isinstance(benchmark, dict):
        raise CompareError(f"Run artifact is missing benchmark identity: {path}")
    if benchmark.get("fingerprint_schema_version") != 1:
        raise CompareError(f"Unsupported benchmark fingerprint schema in {path}.")
    if benchmark.get("algorithm") != "sha256":
        raise CompareError(f"Unsupported benchmark fingerprint algorithm in {path}.")
    overall = benchmark.get("overall_sha256")
    if not isinstance(overall, str) or _SHA256_HEX.fullmatch(overall) is None:
        raise CompareError(f"benchmark.overall_sha256 is invalid: {path}")
    for key in ("docs_sha256", "starter_sha256", "tasks_sha256", "verifiers_sha256"):
        value = benchmark.get(key)
        if not isinstance(value, str) or _SHA256_HEX.fullmatch(value) is None:
            raise CompareError(f"benchmark.{key} is invalid: {path}")
    manifest_path = benchmark.get("manifest_path")
    if not isinstance(manifest_path, str) or not _is_relative_manifest(manifest_path):
        raise CompareError(f"benchmark.manifest_path is invalid: {path}")
    return overall, manifest_path


def _is_relative_manifest(value: str) -> bool:
    pure = PurePosixPath(value)
    return bool(value) and not pure.is_absolute() and ".." not in pure.parts and "\\" not in value


def _parse_execution(
    path: Path,
    payload: dict[str, Any],
    schema_version: int,
) -> tuple[bool, bool | None]:
    if schema_version < 5:
        return False, None
    execution = payload.get("execution")
    if not isinstance(execution, dict):
        raise CompareError(f"Run artifact is missing execution health: {path}")
    complete = execution.get("complete")
    blocking = execution.get("blocking_agent_failures")
    unknown = execution.get("unknown_agent_failures")
    if not isinstance(complete, bool) or not isinstance(blocking, int) or not isinstance(unknown, int):
        raise CompareError(f"Run artifact execution health is invalid: {path}")
    return True, complete


def _parse_runtime(
    path: Path,
    payload: dict[str, Any],
    schema_version: int,
) -> tuple[bool, str | None, str | None, str | None, str | None, str | None]:
    if schema_version < 6:
        return False, None, None, None, None, None
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        raise CompareError(f"Run artifact is missing runtime provenance: {path}")
    if runtime.get("schema_version") != 1:
        raise CompareError(f"Unsupported runtime schema in {path}.")
    backend = runtime.get("backend")
    if backend == "local":
        return True, "local", None, None, None, None
    if backend != "docker":
        raise CompareError(f"Run artifact runtime backend is invalid: {path}")
    agent = _require_image_info(path, runtime.get("agent"), "agent")
    verifier = _require_image_info(path, runtime.get("verifier"), "verifier")
    return True, "docker", agent[0], agent[1], verifier[0], verifier[1]


def _require_image_info(path: Path, value: object, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise CompareError(f"Run artifact is missing runtime.{label}: {path}")
    requested = value.get("requested_image")
    image_id = value.get("image_id")
    if not isinstance(requested, str) or not requested:
        raise CompareError(f"runtime.{label}.requested_image is invalid: {path}")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise CompareError(f"runtime.{label}.image_id is invalid: {path}")
    return requested, image_id


def _parse_task(path: Path, item: object, schema_version: int) -> ComparableTask:
    if not isinstance(item, dict):
        raise CompareError(f"Task entry must be an object: {path}")
    task_id = item.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise CompareError(f"Task id must be a non-empty string: {path}")
    passed = item.get("passed")
    if not isinstance(passed, bool):
        raise CompareError(f"Task {task_id!r} is missing a boolean passed field: {path}")
    duration = item.get("duration_seconds")
    if not isinstance(duration, int | float):
        raise CompareError(f"Task {task_id!r} is missing duration_seconds: {path}")
    agent = item.get("agent")
    verifier = item.get("verifier")
    if not isinstance(agent, dict) or not isinstance(agent.get("exit_code"), int):
        raise CompareError(f"Task {task_id!r} is missing agent.exit_code: {path}")
    if not isinstance(verifier, dict) or not isinstance(verifier.get("exit_code"), int):
        raise CompareError(f"Task {task_id!r} is missing verifier.exit_code: {path}")
    failure_kind, failure_blocking, failure_recorded = _parse_task_failure(
        path,
        task_id,
        agent,
        schema_version,
    )
    return ComparableTask(
        task_id=task_id,
        passed=passed,
        agent_exit=agent["exit_code"],
        verifier_exit=verifier["exit_code"],
        duration_seconds=float(duration),
        failure_kind=failure_kind,
        failure_blocking=failure_blocking,
        failure_recorded=failure_recorded,
    )


def _parse_task_failure(
    path: Path,
    task_id: str,
    agent: dict[str, Any],
    schema_version: int,
) -> tuple[str | None, bool, bool]:
    if schema_version < 5:
        return None, False, False
    if "failure" not in agent:
        raise CompareError(f"Task {task_id!r} is missing agent.failure: {path}")
    failure = agent["failure"]
    if failure is None:
        return None, False, True
    if not isinstance(failure, dict):
        raise CompareError(f"Task {task_id!r} agent.failure is invalid: {path}")
    kind = failure.get("kind")
    blocking = failure.get("blocking")
    if kind not in _FAILURE_KINDS or not isinstance(blocking, bool):
        raise CompareError(f"Task {task_id!r} agent.failure is invalid: {path}")
    return kind, blocking, True


def _require_text(payload: dict[str, Any], key: str, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CompareError(f"Run artifact is missing {key}: {path}")
    return value
