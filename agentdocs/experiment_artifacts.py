"""Persist a documentation experiment without copying child-run logs."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentdocs.artifacts import ArtifactError
from agentdocs.experiment import (
    DocsExperimentRunResult,
    DocsVariantRunResult,
    TaskTransition,
    aggregate_transition_counts,
    experiment_comparability,
    experiment_status,
    variant_status_label,
)
from agentdocs.fingerprint import TreeChangeManifest
from agentdocs.markdown_text import markdown_table_cell

EXPERIMENT_SCHEMA_VERSION = 1
_RUN_ID_ATTEMPTS = 8
_PATH_LIST_LIMIT = 20
_OBSERVATION_NOTE = (
    "These are observed outcomes from this experiment. "
    "One run per variant does not establish statistical significance or causal effect."
)
_SOURCE_NOTE = (
    "Fingerprints describe the sources observed when each variant was hashed. "
    "They are not a lock against another process editing those sources during the experiment."
)


class ExperimentArtifactError(ArtifactError):
    """Raised when the experiment parent directory cannot be written."""


@dataclass(frozen=True)
class ExperimentArtifacts:
    """Locations of one persisted documentation experiment."""

    experiment_run_id: str
    root: Path
    experiment_json: Path
    summary_markdown: Path


def write_experiment_artifacts(
    result: DocsExperimentRunResult,
    *,
    run_ids: dict[str, str],
    output_root: Path,
) -> ExperimentArtifacts:
    """Write ``experiment.json`` and ``summary.md`` for a finished experiment.

    ``run_ids`` maps a completed variant id to its normal run id. Raw logs
    are not copied. Documentation contents and absolute docs paths are omitted.
    """
    root = Path(output_root)
    _ensure_output_root(root)
    experiment_run_id = _allocate_experiment_run_id(root)
    created_at = datetime.now(timezone.utc)
    staging = root / f".experiment-{experiment_run_id}.tmp"
    destination = root / experiment_run_id
    try:
        staging.mkdir()
        _write_experiment_directory(staging, result, run_ids, experiment_run_id, created_at)
        if destination.exists():
            raise ExperimentArtifactError(f"Experiment directory already exists: {destination}")
        os.rename(staging, destination)
    except ExperimentArtifactError:
        _remove_staging(staging)
        raise
    except OSError as exc:
        _remove_staging(staging)
        raise ExperimentArtifactError(f"Could not write experiment artifacts: {exc}") from exc
    return ExperimentArtifacts(
        experiment_run_id=experiment_run_id,
        root=destination,
        experiment_json=destination / "experiment.json",
        summary_markdown=destination / "summary.md",
    )


def experiment_summary_markdown(
    result: DocsExperimentRunResult,
    *,
    experiment_run_id: str,
    run_ids: dict[str, str],
) -> str:
    """Markdown report. Paths, prompts, logs, and environment values are omitted."""
    comparability = experiment_comparability(result)
    model = "provider default" if not result.requested_model else result.requested_model
    lines = [
        "# AgentDocsBench Documentation Experiment",
        "",
        f"Reference: `{markdown_table_cell(result.reference_variant)}`",
        f"Agent: `{markdown_table_cell(result.agent_type)}`",
        f"Model: `{markdown_table_cell(model)}`",
        f"Runtime: `{markdown_table_cell(result.runtime_backend)}`",
        "",
        "## Controlled benchmark components",
        "",
        f"Starter: `sha256:{result.controlled_starter_sha256}`",
        f"Tasks: `sha256:{result.controlled_tasks_sha256}`",
        f"Verifiers: `sha256:{result.controlled_verifiers_sha256}`",
        "",
        _SOURCE_NOTE,
        "",
        "## Variants",
        "",
        "| Variant | Status | Docs fingerprint | Tasks | Run |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for variant in result.variants:
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_table_cell(variant.variant_id),
                    variant_status_label(variant),
                    _docs_cell(variant),
                    _tasks_cell(variant),
                    markdown_table_cell(run_ids.get(variant.variant_id, "")),
                )
            )
            + " |"
        )
    for comparison in result.comparisons:
        lines.extend(["", f"## {markdown_table_cell(comparison.variant_id)} vs {markdown_table_cell(comparison.reference_variant)}", ""])
        lines.extend(_docs_change_lines(comparison.docs_changes))
        lines.extend(
            [
                "",
                "| Task | "
                + markdown_table_cell(comparison.reference_variant)
                + " | "
                + markdown_table_cell(comparison.variant_id)
                + " | Transition |",
                "| --- | --- | --- | --- |",
            ]
        )
        for task in comparison.tasks:
            lines.append(
                "| "
                + " | ".join(
                    (
                        markdown_table_cell(task.task_id),
                        _result_cell(task.reference_result),
                        _result_cell(task.variant_result),
                        markdown_table_cell(task.transition),
                    )
                )
                + " |"
            )
        counts = _comparison_counts(comparison.tasks)
        lines.extend(
            [
                "",
                "Summary:",
                "",
                f"newly passing: {counts['newly_passing']}",
                f"newly failing: {counts['newly_failing']}",
                f"unchanged pass: {counts['unchanged_pass']}",
                f"unchanged fail: {counts['unchanged_fail']}",
                f"not comparable: {counts['not_comparable']}",
            ]
        )
    lines.extend(["", "## Comparability", ""])
    lines.extend(_comparability_lines(result, comparability))
    lines.extend(["", _OBSERVATION_NOTE, ""])
    if experiment_run_id:
        lines.extend([f"Experiment run id: `{markdown_table_cell(experiment_run_id)}`", ""])
    return "\n".join(lines)


def _write_experiment_directory(
    directory: Path,
    result: DocsExperimentRunResult,
    run_ids: dict[str, str],
    experiment_run_id: str,
    created_at: datetime,
) -> None:
    payload = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": experiment_run_id,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_variant": result.reference_variant,
        "status": experiment_status(result),
        "agent": {
            "type": result.agent_type,
            "requested_model": result.requested_model,
        },
        "runtime_backend": result.runtime_backend,
        "controlled_components": {
            "starter_sha256": result.controlled_starter_sha256,
            "tasks_sha256": result.controlled_tasks_sha256,
            "verifiers_sha256": result.controlled_verifiers_sha256,
        },
        "reference_benchmark_sha256": result.reference_benchmark_sha256,
        "variants": [_variant_payload(variant, run_ids.get(variant.variant_id)) for variant in result.variants],
        "comparisons": [_comparison_payload(comparison) for comparison in result.comparisons],
        "transition_counts": aggregate_transition_counts(result),
        "comparability": _comparability_payload(result),
    }
    (directory / "experiment.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (directory / "summary.md").write_text(
        experiment_summary_markdown(
            result,
            experiment_run_id=experiment_run_id,
            run_ids=run_ids,
        ),
        encoding="utf-8",
    )


def _variant_payload(variant: DocsVariantRunResult, run_id: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": variant.variant_id,
        "position": variant.position,
        "status": variant.status,
        "docs_sha256": variant.fingerprint.docs_sha256 if variant.fingerprint else None,
        "benchmark_sha256": variant.fingerprint.overall_sha256 if variant.fingerprint else None,
    }
    if variant.suite_result is None:
        payload["error_type"] = variant.error_type
        payload["error_message"] = variant.error_message
        payload["execution"] = {"complete": False}
        return payload
    suite = variant.suite_result
    payload["run_id"] = run_id
    payload["summary"] = {
        "total": suite.total,
        "passed": suite.passed_count,
        "failed": suite.failed_count,
    }
    payload["execution"] = {
        "complete": suite.execution_complete,
        "blocking_agent_failures": suite.blocking_agent_failure_count,
        "unknown_agent_failures": suite.unknown_agent_failure_count,
    }
    if variant.status == "provider_error":
        payload["provider_failures"] = suite.blocking_failure_counts()
    return payload


def _comparison_payload(comparison) -> dict[str, object]:
    counts = _comparison_counts(comparison.tasks)
    return {
        "variant_id": comparison.variant_id,
        "reference_variant": comparison.reference_variant,
        "docs_changes": _docs_change_payload(comparison.docs_changes),
        "transition_counts": counts,
        "tasks": [
            {
                "id": task.task_id,
                "reference_result": task.reference_result,
                "variant_result": task.variant_result,
                "transition": task.transition,
            }
            for task in comparison.tasks
        ],
    }


def _docs_change_payload(changes: TreeChangeManifest | None) -> dict[str, object] | None:
    if changes is None:
        return None
    return {
        "files_added": list(changes.added_files),
        "files_removed": list(changes.removed_files),
        "files_modified": list(changes.modified_files),
        "directories_added": list(changes.added_directories),
        "directories_removed": list(changes.removed_directories),
        "type_changes": [
            {"path": item.path, "before": item.before, "after": item.after}
            for item in changes.type_changes
        ],
    }


def _comparability_payload(result: DocsExperimentRunResult) -> dict[str, object]:
    info = experiment_comparability(result)
    return {
        "resolved_models": list(info.resolved_models),
        "resolved_model_differs": info.resolved_model_differs,
        "agent_image_ids": list(info.agent_image_ids),
        "verifier_image_ids": list(info.verifier_image_ids),
        "runtime_image_ids_differ": info.runtime_image_ids_differ,
    }


def _comparison_counts(tasks: tuple[TaskTransition, ...]) -> dict[str, int]:
    counts = {
        "newly_passing": 0,
        "newly_failing": 0,
        "unchanged_pass": 0,
        "unchanged_fail": 0,
        "not_comparable": 0,
    }
    for task in tasks:
        counts[task.transition] += 1
    return counts


def _docs_change_lines(changes: TreeChangeManifest | None) -> list[str]:
    if changes is None:
        return ["Documentation changes: unavailable."]
    return [
        "Documentation changes:",
        "",
        f"- {_count_label(len(changes.modified_files), 'modified file')}",
        f"- {_count_label(len(changes.added_files), 'added file')}",
        f"- {_count_label(len(changes.removed_files), 'removed file')}",
        f"- {_count_label(len(changes.added_directories), 'added directory', 'added directories')}",
        f"- {_count_label(len(changes.removed_directories), 'removed directory', 'removed directories')}",
        f"- {_count_label(len(changes.type_changes), 'type change')}",
    ]


def _comparability_lines(result: DocsExperimentRunResult, info) -> list[str]:
    model = "provider default" if not result.requested_model else result.requested_model
    lines = [
        "Controlled benchmark components:",
        "- starter: MATCH",
        "- tasks: MATCH",
        "- verifiers: MATCH",
        "",
        f"Requested agent/model: {result.agent_type} / {model}",
    ]
    if not result.requested_model:
        lines.append(
            "The provider controls model resolution. "
            "AgentDocsBench kept the request constant."
        )
    if result.runtime_backend == "local":
        lines.append("Local runtime does not fingerprint the host provider binary.")
    elif info.runtime_image_ids_differ:
        lines.append("Docker image id differed between documentation variants.")
    elif info.agent_image_ids:
        lines.append("Runtime: same Docker image id.")
    else:
        lines.append("Docker image id: not recorded.")
    if info.resolved_model_differs:
        lines.append("Resolved model differed between documentation variants.")
        lines.append("Resolved models: " + ", ".join(info.resolved_models))
    elif info.resolved_models:
        lines.append(f"Resolved model: {info.resolved_models[0]}")
    else:
        lines.append("Resolved model: not reported.")
    return lines


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _docs_cell(variant: DocsVariantRunResult) -> str:
    if variant.fingerprint is None:
        return "—"
    return f"`sha256:{variant.fingerprint.docs_sha256[:12]}`"


def _tasks_cell(variant: DocsVariantRunResult) -> str:
    suite = variant.suite_result
    if suite is None:
        return "—"
    return f"{suite.passed_count}/{suite.total}"


def _result_cell(value: str | None) -> str:
    if value is None:
        return "—"
    return value


def capped_paths(paths: tuple[str, ...], *, limit: int = _PATH_LIST_LIMIT) -> tuple[str, ...]:
    """Return at most ``limit`` paths. The caller can report the remainder."""
    return paths[:limit]


def _ensure_output_root(output_root: Path) -> None:
    if output_root.exists() and not output_root.is_dir():
        raise ExperimentArtifactError(f"Experiment output root is not a directory: {output_root}")
    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExperimentArtifactError(
            f"Could not create experiment output root {output_root}: {exc}"
        ) from exc


def _allocate_experiment_run_id(output_root: Path) -> str:
    for _ in range(_RUN_ID_ATTEMPTS):
        experiment_run_id = _new_experiment_run_id()
        if (output_root / experiment_run_id).exists():
            continue
        if (output_root / f".experiment-{experiment_run_id}.tmp").exists():
            continue
        return experiment_run_id
    raise ExperimentArtifactError("Could not allocate a unique experiment run id.")


def _new_experiment_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{timestamp}-{suffix}"


def _remove_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
