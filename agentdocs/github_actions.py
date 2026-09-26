"""GitHub Actions job summary and step outputs for an existing benchmark result.

This module is presentation. It does not run a suite, a matrix, a verifier,
or a provider. Callers pass the result objects the CLI already produced.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentdocs.execution.runtime_info import RuntimeInfo
from agentdocs.experiment import DocsExperimentRunResult, aggregate_transition_counts, experiment_status
from agentdocs.experiment_artifacts import experiment_summary_markdown
from agentdocs.markdown_text import markdown_table_cell
from agentdocs.matrix import MatrixRunResult, MatrixTargetResult, target_status, target_status_label
from agentdocs.suite import SuiteRunResult
from agentdocs.workspace_changes import file_change_label

SUITE_OUTPUT_KEYS = (
    "status",
    "exit_code",
    "run_id",
    "artifact_path",
    "benchmark_sha256",
    "suite_passed",
    "execution_complete",
    "runtime_backend",
)
MATRIX_OUTPUT_KEYS = (
    "status",
    "exit_code",
    "matrix_run_id",
    "artifact_path",
    "benchmark_sha256",
    "runtime_backend",
    "target_count",
    "pass_targets",
    "fail_targets",
    "provider_error_targets",
    "error_targets",
)
_STATUS_LABELS = {
    "pass": "PASS",
    "fail": "FAIL",
    "provider_error": "PROVIDER_ERROR",
    "error": "ERROR",
    "complete": "COMPLETE",
}
EXPERIMENT_OUTPUT_KEYS = (
    "status",
    "exit_code",
    "experiment_run_id",
    "artifact_path",
    "reference_variant",
    "variant_count",
    "runtime_backend",
    "reference_benchmark_sha256",
    "newly_passing",
    "newly_failing",
    "unchanged_pass",
    "unchanged_fail",
    "not_comparable",
)
_KEY_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


class GitHubActionsError(Exception):
    """GitHub Actions reporting could not run.

    This is CLI integration infrastructure. It is not a provider failure, a
    verifier failure, or a Docker runtime error.
    """


@dataclass(frozen=True)
class GitHubActionsEnvironment:
    """The two GitHub Actions files this command is allowed to append to."""

    summary_path: Path
    output_path: Path


def load_github_actions_environment() -> GitHubActionsEnvironment:
    """Read the GitHub Actions file paths the workflow already provided.

    ``--github-actions`` is opt-in. This function is the only place those
    variables are read, and only after the flag is set.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise GitHubActionsError(
            "--github-actions requires a GitHub Actions job (GITHUB_ACTIONS=true)."
        )
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not summary:
        raise GitHubActionsError("--github-actions requires GITHUB_STEP_SUMMARY.")
    if not output:
        raise GitHubActionsError("--github-actions requires GITHUB_OUTPUT.")
    return GitHubActionsEnvironment(summary_path=Path(summary), output_path=Path(output))


def suite_ci_status(result: SuiteRunResult) -> str:
    """``pass``, ``fail``, or ``provider_error`` from an existing suite result."""
    if not result.execution_complete:
        return "provider_error"
    if not result.passed:
        return "fail"
    return "pass"


def matrix_ci_status(result: MatrixRunResult) -> str:
    """Aggregate status. Infrastructure error outranks provider error, then verifier failure."""
    statuses = [target_status(target) for target in result.targets]
    if "error" in statuses:
        return "error"
    if "provider_error" in statuses:
        return "provider_error"
    if "fail" in statuses:
        return "fail"
    return "pass"


def publish_suite_report(
    environment: GitHubActionsEnvironment,
    result: SuiteRunResult,
    *,
    agent_type: str,
    model: str | None,
    benchmark_sha256: str,
    exit_code: int,
    run_id: str,
    artifact_path: str,
) -> str:
    """Append the suite job summary and step outputs. Return the markdown written."""
    status = suite_ci_status(result)
    markdown = render_suite_summary(
        result,
        agent_type=agent_type,
        model=model,
        benchmark_sha256=benchmark_sha256,
        run_id=run_id,
        status=status,
    )
    outputs = {
        "status": status,
        "exit_code": str(exit_code),
        "run_id": run_id,
        "artifact_path": artifact_path,
        "benchmark_sha256": benchmark_sha256,
        "suite_passed": _bool_text(result.passed),
        "execution_complete": _bool_text(result.execution_complete),
        "runtime_backend": result.runtime.backend,
    }
    _publish(environment, markdown, outputs, SUITE_OUTPUT_KEYS)
    return markdown


def publish_suite_infrastructure_error(
    environment: GitHubActionsEnvironment,
    *,
    benchmark_sha256: str = "",
    runtime_backend: str = "",
) -> str:
    """Append a minimal error summary. The exception text is not included."""
    markdown = render_infrastructure_summary(
        heading="AgentDocsBench",
        benchmark_sha256=benchmark_sha256,
        runtime_backend=runtime_backend,
    )
    outputs = {
        "status": "error",
        "exit_code": "2",
        "run_id": "",
        "artifact_path": "",
        "benchmark_sha256": benchmark_sha256,
        "suite_passed": "",
        "execution_complete": "",
        "runtime_backend": runtime_backend,
    }
    _publish(environment, markdown, outputs, SUITE_OUTPUT_KEYS)
    return markdown


def publish_matrix_report(
    environment: GitHubActionsEnvironment,
    result: MatrixRunResult,
    *,
    benchmark_sha256: str,
    exit_code: int,
    matrix_run_id: str,
    artifact_path: str,
    run_ids: Mapping[str, str],
) -> str:
    """Append the matrix job summary and step outputs. Return the markdown written."""
    status = matrix_ci_status(result)
    markdown = render_matrix_summary(
        result,
        benchmark_sha256=benchmark_sha256,
        matrix_run_id=matrix_run_id,
        run_ids=run_ids,
        status=status,
    )
    counts = _target_counts(result)
    outputs = {
        "status": status,
        "exit_code": str(exit_code),
        "matrix_run_id": matrix_run_id,
        "artifact_path": artifact_path,
        "benchmark_sha256": benchmark_sha256,
        "runtime_backend": result.runtime_backend,
        "target_count": str(len(result.targets)),
        "pass_targets": str(counts["pass"]),
        "fail_targets": str(counts["fail"]),
        "provider_error_targets": str(counts["provider_error"]),
        "error_targets": str(counts["error"]),
    }
    _publish(environment, markdown, outputs, MATRIX_OUTPUT_KEYS)
    return markdown


def publish_matrix_infrastructure_error(
    environment: GitHubActionsEnvironment,
    *,
    benchmark_sha256: str = "",
    runtime_backend: str = "",
) -> str:
    """Append a minimal matrix error summary. The exception text is not included."""
    markdown = render_infrastructure_summary(
        heading="AgentDocsBench Matrix",
        benchmark_sha256=benchmark_sha256,
        runtime_backend=runtime_backend,
    )
    outputs = {
        "status": "error",
        "exit_code": "2",
        "matrix_run_id": "",
        "artifact_path": "",
        "benchmark_sha256": benchmark_sha256,
        "runtime_backend": runtime_backend,
        "target_count": "",
        "pass_targets": "",
        "fail_targets": "",
        "provider_error_targets": "",
        "error_targets": "",
    }
    _publish(environment, markdown, outputs, MATRIX_OUTPUT_KEYS)
    return markdown


def render_suite_summary(
    result: SuiteRunResult,
    *,
    agent_type: str,
    model: str | None,
    benchmark_sha256: str,
    run_id: str,
    status: str,
) -> str:
    """Markdown for one suite. Paths, prompts, logs, and environment values are omitted."""
    lines = [
        "# AgentDocsBench",
        "",
        f"**Status:** {_STATUS_LABELS[status]}",
        "",
    ]
    if status == "provider_error":
        lines.extend(["Provider execution was incomplete.", ""])
    elif status == "fail":
        lines.extend(["Verifier checks failed.", ""])
    lines.extend(
        [
            f"**Agent:** {markdown_table_cell(agent_type)}",
            f"**Model:** {markdown_table_cell(_model_label(model))}",
            f"**Runtime:** {markdown_table_cell(result.runtime.backend)}",
        ]
    )
    lines.extend(_runtime_detail_lines(result.runtime))
    if benchmark_sha256:
        lines.append(f"**Benchmark:** `sha256:{markdown_table_cell(benchmark_sha256)}`")
    lines.extend(
        [
            "",
            "## Result",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Tasks | {result.total} |",
            f"| Passed | {result.passed_count} |",
            f"| Failed | {result.failed_count} |",
            f"| Execution complete | {'yes' if result.execution_complete else 'no'} |",
            f"| Blocking provider failures | {result.blocking_agent_failure_count} |",
            f"| Unknown agent failures | {result.unknown_agent_failure_count} |",
            "",
            "## Tasks",
            "",
            "| Task | Result | Agent | Provider failure | Changes |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for task in result.results:
        failure = task.agent_result.failure
        failure_cell = "—" if failure is None else failure.kind.value
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_table_cell(task.task_id),
                    "PASS" if task.passed else "FAIL",
                    str(task.agent_result.exit_code),
                    markdown_table_cell(failure_cell),
                    markdown_table_cell(file_change_label(task.workspace_changes)),
                )
            )
            + " |"
        )
    blocking = [
        (kind, count) for kind, count in result.blocking_failure_counts().items() if count
    ]
    if blocking:
        lines.extend(["", "## Provider execution", ""])
        for kind, count in blocking:
            noun = "task" if count == 1 else "tasks"
            lines.append(f"{markdown_table_cell(kind)}: {count} {noun}")
    lines.extend(["", "## Artifact", ""])
    if run_id:
        lines.append(f"Run id: `{markdown_table_cell(run_id)}`")
    else:
        lines.append("Run id: not recorded")
    return "\n".join(lines) + "\n"


def render_matrix_summary(
    result: MatrixRunResult,
    *,
    benchmark_sha256: str,
    matrix_run_id: str,
    run_ids: Mapping[str, str],
    status: str,
) -> str:
    """Markdown for one matrix. Target order is the matrix file order."""
    lines = [
        "# AgentDocsBench Matrix",
        "",
        f"**Status:** {_STATUS_LABELS[status]}",
        "",
    ]
    if status == "provider_error":
        lines.extend(["Provider execution was incomplete.", ""])
    elif status == "fail":
        lines.extend(["Verifier checks failed.", ""])
    elif status == "error":
        lines.extend(
            [
                "One or more targets stopped on an infrastructure error. See the step log.",
                "",
            ]
        )
    if benchmark_sha256:
        lines.append(f"**Benchmark:** `sha256:{markdown_table_cell(benchmark_sha256)}`")
    lines.append(f"**Runtime:** {markdown_table_cell(result.runtime_backend)}")
    lines.extend(_matrix_image_lines(result))
    lines.extend(
        [
            "",
            "| Target | Agent | Model | Status | Tasks | Duration |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for target in result.targets:
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_table_cell(target.target_id),
                    markdown_table_cell(target.agent_type),
                    markdown_table_cell(_model_label(target.requested_model)),
                    markdown_table_cell(target_status_label(target)),
                    _tasks_cell(target),
                    f"{target.duration_seconds:.1f}s",
                )
            )
            + " |"
        )
    blocking = _matrix_blocking_counts(result)
    if blocking:
        lines.extend(["", "## Provider execution", ""])
        for kind, count in blocking:
            noun = "task" if count == 1 else "tasks"
            lines.append(f"{markdown_table_cell(kind)}: {count} {noun}")
    unknown = _unknown_count(result)
    if unknown:
        lines.extend(["", f"Unknown agent failures: {unknown}"])
    recorded = [
        (target.target_id, run_ids[target.target_id])
        for target in result.targets
        if target.target_id in run_ids
    ]
    if matrix_run_id or recorded:
        lines.extend(["", "## Runs", ""])
        if matrix_run_id:
            lines.append(f"Matrix run id: `{markdown_table_cell(matrix_run_id)}`")
        for target_id, run_id in recorded:
            lines.append(
                f"- `{markdown_table_cell(target_id)}`: `{markdown_table_cell(run_id)}`"
            )
    return "\n".join(lines) + "\n"


def render_infrastructure_summary(
    *,
    heading: str,
    benchmark_sha256: str,
    runtime_backend: str,
) -> str:
    """Short summary used when no completed result exists."""
    lines = [
        f"# {heading}",
        "",
        "**Status:** ERROR",
        "",
        "AgentDocsBench could not complete the benchmark.",
        "",
        "See the step log for the infrastructure error.",
    ]
    if runtime_backend:
        lines.extend(["", f"**Runtime:** {markdown_table_cell(runtime_backend)}"])
    if benchmark_sha256:
        lines.extend(["", f"**Benchmark:** `sha256:{markdown_table_cell(benchmark_sha256)}`"])
    return "\n".join(lines) + "\n"


def append_step_summary(path: Path, markdown: str) -> None:
    """Append markdown to ``GITHUB_STEP_SUMMARY`` without replacing earlier steps."""
    _append_text(path, markdown, label="GITHUB_STEP_SUMMARY")


def append_github_output(path: Path, values: Mapping[str, str], allowed: tuple[str, ...]) -> None:
    """Append step outputs using GitHub's multiline environment-file form."""
    if tuple(values) != allowed:
        raise GitHubActionsError("GitHub output keys did not match the stable set.")
    chunks: list[str] = []
    for key, value in values.items():
        if not key or any(character not in _KEY_CHARS for character in key):
            raise GitHubActionsError("Refusing to write an unsupported GitHub output key.")
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        delimiter = _delimiter(normalized)
        chunks.append(f"{key}<<{delimiter}\n{normalized}\n{delimiter}\n")
    _append_text(path, "".join(chunks), label="GITHUB_OUTPUT")


def _publish(
    environment: GitHubActionsEnvironment,
    markdown: str,
    outputs: Mapping[str, str],
    allowed: tuple[str, ...],
) -> None:
    append_step_summary(environment.summary_path, markdown)
    append_github_output(environment.output_path, outputs, allowed)


def _append_text(path: Path, text: str, *, label: str) -> None:
    payload = text if text.endswith("\n") else text + "\n"
    try:
        exists = path.exists()
        if exists and path.is_dir():
            raise OSError(f"{label} is a directory")
        needs_break = exists and path.stat().st_size > 0 and not _ends_with_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            if needs_break:
                handle.write("\n")
            handle.write(payload)
    except OSError as exc:
        raise GitHubActionsError(f"Could not append to {label}.") from exc


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            return True
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) == b"\n"


def _delimiter(value: str) -> str:
    for _ in range(8):
        token = f"agentdocs_{uuid.uuid4().hex}"
        if token not in value.split("\n"):
            return token
    raise GitHubActionsError("Could not allocate a GitHub output delimiter.")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _model_label(model: str | None) -> str:
    if model is None or model == "":
        return "provider default"
    return model


def _runtime_detail_lines(runtime: RuntimeInfo) -> list[str]:
    if runtime.backend != "docker":
        return []
    lines: list[str] = []
    if runtime.agent is not None:
        lines.append(f"**Agent image:** `{markdown_table_cell(runtime.agent.requested_image)}`")
        lines.append(f"**Agent network:** {markdown_table_cell(runtime.agent.network)}")
    if runtime.verifier is not None:
        lines.append(
            f"**Verifier image:** `{markdown_table_cell(runtime.verifier.requested_image)}`"
        )
        lines.append(f"**Verifier network:** {markdown_table_cell(runtime.verifier.network)}")
    return lines


def _matrix_image_lines(result: MatrixRunResult) -> list[str]:
    images: list[tuple[str, str]] = []
    for target in result.targets:
        suite = target.suite_result
        if suite is None or suite.runtime.agent is None:
            continue
        images.append((target.target_id, suite.runtime.agent.requested_image))
    if not images:
        return []
    unique = {image for _target_id, image in images}
    if len(unique) == 1:
        return [f"**Agent image:** `{markdown_table_cell(images[0][1])}`"]
    lines = ["**Agent images:**"]
    for target_id, image in images:
        lines.append(f"- `{markdown_table_cell(target_id)}`: `{markdown_table_cell(image)}`")
    return lines


def _tasks_cell(target: MatrixTargetResult) -> str:
    suite = target.suite_result
    if suite is None:
        return "—"
    return f"{suite.passed_count}/{suite.total}"


def _target_counts(result: MatrixRunResult) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "provider_error": 0, "error": 0}
    for target in result.targets:
        counts[target_status(target)] += 1
    return counts


def _matrix_blocking_counts(result: MatrixRunResult) -> list[tuple[str, int]]:
    totals: dict[str, int] = {}
    order: list[str] = []
    for target in result.targets:
        suite = target.suite_result
        if suite is None:
            continue
        for kind, count in suite.blocking_failure_counts().items():
            if kind not in totals:
                totals[kind] = 0
                order.append(kind)
            totals[kind] += count
    return [(kind, totals[kind]) for kind in order if totals[kind]]


def publish_experiment_report(
    environment: GitHubActionsEnvironment,
    result: DocsExperimentRunResult,
    *,
    exit_code: int,
    experiment_run_id: str,
    artifact_path: str,
    run_ids: Mapping[str, str],
) -> str:
    """Append the experiment job summary and step outputs."""
    status = experiment_status(result)
    markdown = render_experiment_summary(
        result,
        experiment_run_id=experiment_run_id,
        run_ids=run_ids,
        status=status,
    )
    counts = aggregate_transition_counts(result)
    outputs = {
        "status": status,
        "exit_code": str(exit_code),
        "experiment_run_id": experiment_run_id,
        "artifact_path": artifact_path,
        "reference_variant": result.reference_variant,
        "variant_count": str(len(result.variants)),
        "runtime_backend": result.runtime_backend,
        "reference_benchmark_sha256": result.reference_benchmark_sha256,
        "newly_passing": str(counts["newly_passing"]),
        "newly_failing": str(counts["newly_failing"]),
        "unchanged_pass": str(counts["unchanged_pass"]),
        "unchanged_fail": str(counts["unchanged_fail"]),
        "not_comparable": str(counts["not_comparable"]),
    }
    _publish(environment, markdown, outputs, EXPERIMENT_OUTPUT_KEYS)
    return markdown


def publish_experiment_infrastructure_error(
    environment: GitHubActionsEnvironment,
    *,
    reference_variant: str = "",
    variant_count: str = "",
    runtime_backend: str = "",
) -> str:
    """Append a minimal experiment error summary. The exception text is not included."""
    markdown = render_infrastructure_summary(
        heading="AgentDocsBench Documentation Experiment",
        benchmark_sha256="",
        runtime_backend=runtime_backend,
    )
    outputs = {
        "status": "error",
        "exit_code": "2",
        "experiment_run_id": "",
        "artifact_path": "",
        "reference_variant": reference_variant,
        "variant_count": variant_count,
        "runtime_backend": runtime_backend,
        "reference_benchmark_sha256": "",
        "newly_passing": "",
        "newly_failing": "",
        "unchanged_pass": "",
        "unchanged_fail": "",
        "not_comparable": "",
    }
    _publish(environment, markdown, outputs, EXPERIMENT_OUTPUT_KEYS)
    return markdown


def render_experiment_summary(
    result: DocsExperimentRunResult,
    *,
    experiment_run_id: str,
    run_ids: Mapping[str, str],
    status: str,
) -> str:
    """Experiment job summary. Newly failing tasks do not change ``status``."""
    body = experiment_summary_markdown(
        result,
        experiment_run_id=experiment_run_id,
        run_ids=dict(run_ids),
    )
    lines = body.splitlines()
    inserted = [lines[0], "", f"**Status:** {_STATUS_LABELS[status]}", ""]
    if status == "provider_error":
        inserted.extend(["Provider execution was incomplete.", ""])
    elif status == "error":
        inserted.extend(
            ["One or more variants stopped on an infrastructure error. See the step log.", ""]
        )
    inserted.extend(lines[1:])
    return "\n".join(inserted) + "\n"


def _unknown_count(result: MatrixRunResult) -> int:
    total = 0
    for target in result.targets:
        suite = target.suite_result
        if suite is not None:
            total += suite.unknown_agent_failure_count
    return total
