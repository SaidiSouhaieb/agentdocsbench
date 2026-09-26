"""Render matrix progress and factual comparison tables."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from agentdocs.compare import (
    ComparableRun,
    ComparableTask,
    classify_benchmark_identity,
    comparison_task_ids,
    task_sets_match,
)
from agentdocs.fingerprint import (
    BenchmarkChangeManifest,
    BenchmarkFingerprintError,
    TreeChangeManifest,
    compare_benchmark_fingerprints,
    load_benchmark_manifest,
)
from agentdocs.matrix import (
    MatrixRunResult,
    MatrixTargetResult,
    target_status,
    target_status_label,
)
from agentdocs.matrix_progress import (
    MatrixFinished,
    MatrixProgressEvent,
    MatrixStarted,
    MatrixTargetFailed,
    MatrixTargetFinished,
    MatrixTargetStarted,
)


class MatrixCliReporter:
    """Print which matrix target is running. Quiet mode prints nothing."""

    def __init__(self, console: Console, *, quiet: bool = False) -> None:
        self._console = console
        self._quiet = quiet

    def __call__(self, event: MatrixProgressEvent) -> None:
        if self._quiet:
            return
        if isinstance(event, MatrixTargetStarted):
            model = event.requested_model or "provider default"
            self._console.print(
                f"Target [{event.index}/{event.total}]: {event.target_id}"
            )
            self._console.print(f"Agent: {event.agent}")
            self._console.print(f"Model: {model}")
            self._console.print()
            return
        if isinstance(event, MatrixTargetFinished):
            if event.execution_complete:
                self._console.print(
                    f"Target {event.target_id} completed: "
                    f"{event.passed} passed, {event.failed} failed"
                )
            else:
                self._console.print(
                    f"Target {event.target_id} provider error: "
                    f"{event.passed} passed, {event.failed} failed"
                )
            self._console.print()
            return
        if isinstance(event, MatrixTargetFailed):
            self._console.print(
                f"Target {event.target_id} error: {event.error_type}: {event.error_message}"
            )
            self._console.print()
            return
        if isinstance(event, (MatrixStarted, MatrixFinished)):
            return


def render_matrix_report(
    console: Console,
    result: MatrixRunResult,
    task_ids: tuple[str, ...],
) -> None:
    """Print per-task results and target counts. No rank or winner."""
    task_table = Table(show_header=True, header_style="bold")
    task_table.add_column("Task")
    for target in result.targets:
        task_table.add_column(target.target_id)
    for task_id in task_ids:
        task_table.add_row(
            task_id,
            *[_matrix_task_cell(target, task_id) for target in result.targets],
        )
    console.print(task_table)
    console.print()

    summary = Table(show_header=True, header_style="bold")
    summary.add_column("Target")
    summary.add_column("Agent")
    summary.add_column("Model")
    summary.add_column("Status", overflow="fold", no_wrap=True)
    summary.add_column("Passed", justify="right")
    summary.add_column("Failed", justify="right")
    summary.add_column("Duration", justify="right")
    for target in result.targets:
        if target.suite_result is None:
            passed = "ERROR"
            failed = "ERROR"
        else:
            passed = str(target.suite_result.passed_count)
            failed = str(target.suite_result.failed_count)
        summary.add_row(
            target.target_id,
            target.agent_type,
            target.requested_model or "provider default",
            target_status_label(target),
            passed,
            failed,
            f"{target.duration_seconds:.1f}s",
        )
    console.print(summary)
    _render_provider_errors(console, result)
    errors = [target for target in result.targets if not target.completed]
    if not errors:
        return
    console.print()
    console.print("Target errors:")
    console.print()
    for target in errors:
        console.print(f"{target.target_id}: {target.error_type}: {target.error_message}")


def render_compare(console: Console, runs: tuple[ComparableRun, ...]) -> None:
    """Print a factual comparison of saved runs."""
    console.print("AgentDocsBench Compare")
    console.print()
    labels = [_column_label(index) for index in range(len(runs))]
    for label, run in zip(labels, runs, strict=True):
        console.print(f"{label} = {run.agent_type} / {_model_text(run)} / {run.run_id}")
    console.print()
    _render_benchmark_identity(console, runs, labels)
    if not task_sets_match(runs):
        console.print(
            "Warning: task sets differ; these runs may not represent the same benchmark."
        )
        console.print()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Task")
    for label in labels:
        table.add_column(label)
    lookups = [_task_map(run) for run in runs]
    for task_id in comparison_task_ids(runs):
        cells = []
        for lookup in lookups:
            task = lookup.get(task_id)
            cells.append("—" if task is None else ("PASS" if task.passed else "FAIL"))
        table.add_row(task_id, *cells)
    console.print(table)
    console.print()
    for label, run in zip(labels, runs, strict=True):
        console.print(
            f"{label}: {run.passed_count}/{len(run.tasks)} · {run.duration_seconds:.1f}s"
        )
        console.print(f"{label} execution: {run.execution_summary()}")
        console.print(f"{label} runtime: {run.runtime_summary()}")
    if _runtime_differs(runs):
        console.print()
        console.print("Execution runtime differs between artifacts.")


def _runtime_differs(runs: tuple[ComparableRun, ...]) -> bool:
    recorded = [run for run in runs if run.runtime_recorded and run.runtime_backend is not None]
    if len(recorded) < 2:
        return False
    identities = {
        (run.runtime_backend, run.runtime_agent_image_id, run.runtime_verifier_image_id)
        for run in recorded
    }
    return len(identities) > 1


def matrix_exit_code(result: MatrixRunResult) -> int:
    """Return 2 for infrastructure or blocking provider failure, else verifier status."""
    if any(not target.completed for target in result.targets):
        return 2
    if any(target_status(target) == "provider_error" for target in result.targets):
        return 2
    if any(
        target.suite_result is not None and not target.suite_result.passed
        for target in result.targets
    ):
        return 1
    return 0


def _render_provider_errors(console: Console, result: MatrixRunResult) -> None:
    targets = [
        target for target in result.targets if target_status(target) == "provider_error"
    ]
    if not targets:
        return
    console.print()
    console.print("Provider errors:")
    console.print()
    for target in targets:
        suite = target.suite_result
        assert suite is not None
        parts = [
            f"{kind}: {count} tasks"
            for kind, count in suite.blocking_failure_counts().items()
            if count
        ]
        detail = ", ".join(parts) if parts else "blocking provider failure"
        console.print(f"{target.target_id}: PROVIDER_ERROR · {detail}")


def _matrix_task_cell(target: MatrixTargetResult, task_id: str) -> str:
    if target.suite_result is None:
        return "ERROR"
    for task in target.suite_result.results:
        if task.task_id == task_id:
            return "PASS" if task.passed else "FAIL"
    return "—"


def _task_map(run: ComparableRun) -> dict[str, ComparableTask]:
    return {task.task_id: task for task in run.tasks}


def _render_benchmark_identity(
    console: Console,
    runs: tuple[ComparableRun, ...],
    labels: list[str],
) -> None:
    identity = classify_benchmark_identity(runs)
    if identity == "unverified":
        _plain(console, "Benchmark identity: UNVERIFIED")
        _plain(console, "One or more artifacts predate benchmark fingerprinting.")
        recorded = [run for run in runs if run.benchmark_recorded]
        if recorded and len(recorded) != len(runs):
            console.print()
            for label, run in zip(labels, runs, strict=True):
                if run.benchmark_recorded:
                    _plain(console, f"{label}: sha256:{run.benchmark_overall_sha256}")
                else:
                    _plain(console, f"{label}: not recorded")
        console.print()
        return
    if identity == "verified_match":
        _plain(console, "Benchmark identity: VERIFIED MATCH")
        _plain(console, f"Fingerprint: sha256:{runs[0].benchmark_overall_sha256}")
        console.print()
        return
    _plain(console, "Benchmark identity: DIFFERENT")
    console.print()
    if len(runs) == 2:
        for label, run in zip(labels, runs, strict=True):
            _plain(console, f"{label}: sha256:{run.benchmark_overall_sha256}")
        console.print()
        _render_pairwise_changes(console, runs, labels)
        return
    groups: dict[str, list[str]] = {}
    for label, run in zip(labels, runs, strict=True):
        digest = run.benchmark_overall_sha256 or ""
        groups.setdefault(digest, []).append(label)
    for digest, group_labels in groups.items():
        _plain(console, f"sha256:{digest}")
        _plain(console, "  " + ", ".join(group_labels))
    console.print()
    _plain(console, "Detailed change manifest is available when comparing two runs.")
    console.print()


def _render_pairwise_changes(
    console: Console,
    runs: tuple[ComparableRun, ...],
    labels: list[str],
) -> None:
    loaded = []
    for label, run in zip(labels, runs, strict=True):
        try:
            loaded.append(load_benchmark_manifest(_manifest_path(run)))
        except BenchmarkFingerprintError as exc:
            _plain(console, f"Detailed benchmark manifest unavailable for run {label}.")
            _plain(console, str(exc))
            console.print()
            return
    _render_change_manifest(
        console,
        compare_benchmark_fingerprints(loaded[0], loaded[1]),
    )


def _manifest_path(run: ComparableRun) -> Path:
    relative = run.benchmark_manifest_path or "benchmark.json"
    return run.path.parent / relative


def _render_change_manifest(console: Console, manifest: BenchmarkChangeManifest) -> None:
    _plain(console, "Benchmark changes:")
    console.print()
    _render_tree_section(console, "Docs", manifest.docs)
    _render_tree_section(console, "Starter", manifest.starter)
    _render_task_section(console, manifest)
    _render_verifier_section(console, manifest)
    console.print()


def _render_tree_section(console: Console, title: str, changes: TreeChangeManifest) -> None:
    _plain(console, title)
    if changes.empty:
        _plain(console, "  no changes")
        return
    _render_path_group(console, "added", changes.added_files)
    _render_path_group(console, "removed", changes.removed_files)
    _render_path_group(console, "modified", changes.modified_files)
    _render_path_group(console, "added directories", changes.added_directories)
    _render_path_group(console, "removed directories", changes.removed_directories)
    if changes.type_changes:
        _plain(console, "  type changed:")
        for change in changes.type_changes:
            _plain(console, f"    {change.path}: {change.before} → {change.after}")


def _render_task_section(console: Console, manifest: BenchmarkChangeManifest) -> None:
    tasks = manifest.tasks
    _plain(console, "Tasks")
    if tasks.empty:
        _plain(console, "  no changes")
        return
    _render_path_group(console, "added", tasks.added_task_ids)
    _render_path_group(console, "removed", tasks.removed_task_ids)
    if tasks.reordered:
        _plain(console, "  order changed")
    for change in tasks.definition_changes:
        _plain(console, f"  {change.task_id}:")
        if change.prompt_changed:
            _plain(console, "    prompt changed")
        if change.verifier_command_changed:
            _plain(console, "    verifier command changed")


def _render_verifier_section(console: Console, manifest: BenchmarkChangeManifest) -> None:
    _plain(console, "Verifiers")
    if not manifest.verifiers:
        _plain(console, "  no changes")
        return
    for item in manifest.verifiers:
        _plain(console, f"  {item.task_id}:")
        changes = item.changes
        _render_path_group(console, "added", changes.added_files, indent="    ")
        _render_path_group(console, "removed", changes.removed_files, indent="    ")
        _render_path_group(console, "modified", changes.modified_files, indent="    ")
        _render_path_group(console, "added directories", changes.added_directories, indent="    ")
        _render_path_group(console, "removed directories", changes.removed_directories, indent="    ")
        if changes.type_changes:
            _plain(console, "    type changed:")
            for change in changes.type_changes:
                _plain(console, f"      {change.path}: {change.before} → {change.after}")


def _render_path_group(
    console: Console,
    label: str,
    paths: tuple[str, ...],
    *,
    indent: str = "  ",
) -> None:
    if not paths:
        return
    _plain(console, f"{indent}{label}:")
    for path in paths:
        _plain(console, f"{indent}  {path}")


def _plain(console: Console, text: str) -> None:
    console.print(text, markup=False, highlight=False, soft_wrap=True)


def _model_text(run: ComparableRun) -> str:
    if not run.requested_model_recorded:
        return "not recorded"
    if run.requested_model is None:
        return "provider default"
    return run.requested_model


def _column_label(index: int) -> str:
    label = ""
    number = index
    while True:
        label = chr(ord("A") + number % 26) + label
        number = number // 26 - 1
        if number < 0:
            return label
