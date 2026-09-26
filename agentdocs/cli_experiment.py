"""Render documentation-experiment progress and the final observation report."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from agentdocs.experiment import (
    DocsExperimentRunResult,
    experiment_comparability,
    variant_status_label,
)
from agentdocs.experiment_artifacts import capped_paths
from agentdocs.experiment_progress import (
    ExperimentFinished,
    ExperimentProgressEvent,
    ExperimentStarted,
    VariantError,
    VariantFinished,
    VariantStarted,
)

_OBSERVATION_NOTE = (
    "These are observed outcomes from this experiment. "
    "One run per variant does not establish statistical significance or causal effect."
)
_PATH_LIST_LIMIT = 20


class ExperimentCliReporter:
    """Print which documentation variant is running. Quiet mode prints nothing."""

    def __init__(self, console: Console, *, quiet: bool = False) -> None:
        self._console = console
        self._quiet = quiet

    def __call__(self, event: ExperimentProgressEvent) -> None:
        if self._quiet:
            return
        if isinstance(event, VariantStarted):
            self._console.print(f"Variant [{event.index}/{event.total}] {event.variant_id}")
            self._console.print()
            return
        if isinstance(event, VariantFinished):
            self._console.print(
                f"Variant {event.variant_id} {event.status}: "
                f"{event.passed} passed, {event.failed} failed"
            )
            self._console.print()
            return
        if isinstance(event, VariantError):
            self._console.print(
                f"Variant {event.variant_id} error: {event.error_type}: {event.error_message}"
            )
            self._console.print()
            return
        if isinstance(event, (ExperimentStarted, ExperimentFinished)):
            return


def render_experiment_report(
    console: Console,
    result: DocsExperimentRunResult,
    run_ids: dict[str, str],
    *,
    verbose: bool,
) -> None:
    """Print variant outcomes and reference comparisons. This does not rank variants."""
    _render_variants(console, result, run_ids, verbose=verbose)
    for comparison in result.comparisons:
        console.print(f"{comparison.variant_id} vs {comparison.reference_variant}")
        console.print()
        if comparison.docs_changes is None:
            console.print("Documentation changes: unavailable.")
        else:
            changes = comparison.docs_changes
            console.print(
                "Documentation changes: "
                f"{len(changes.modified_files)} modified, "
                f"{len(changes.added_files)} added, "
                f"{len(changes.removed_files)} removed"
            )
            if verbose:
                _print_capped(console, "modified", changes.modified_files)
                _print_capped(console, "added", changes.added_files)
                _print_capped(console, "removed", changes.removed_files)
        console.print()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Task")
        table.add_column(comparison.reference_variant)
        table.add_column(comparison.variant_id)
        table.add_column("Transition")
        for task in comparison.tasks:
            table.add_row(
                task.task_id,
                task.reference_result or "—",
                task.variant_result or "—",
                task.transition,
            )
        console.print(table)
        console.print()
        counts = {name: 0 for name in (
            "newly_passing",
            "newly_failing",
            "unchanged_pass",
            "unchanged_fail",
            "not_comparable",
        )}
        for task in comparison.tasks:
            counts[task.transition] += 1
        console.print(f"newly passing: {counts['newly_passing']}")
        console.print(f"newly failing: {counts['newly_failing']}")
        console.print(f"unchanged pass: {counts['unchanged_pass']}")
        console.print(f"unchanged fail: {counts['unchanged_fail']}")
        console.print(f"not comparable: {counts['not_comparable']}")
        console.print()
    _render_comparability(console, result, verbose=verbose)
    console.print(_OBSERVATION_NOTE)


def _render_variants(
    console: Console,
    result: DocsExperimentRunResult,
    run_ids: dict[str, str],
    *,
    verbose: bool,
) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Variant")
    table.add_column("Status")
    table.add_column("Passed", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Execution")
    for variant in result.variants:
        suite = variant.suite_result
        if suite is None:
            passed = "—"
            failed = "—"
            execution = "error"
        else:
            passed = str(suite.passed_count)
            failed = str(suite.failed_count)
            execution = "complete" if suite.execution_complete else "incomplete"
        table.add_row(variant.variant_id, variant_status_label(variant), passed, failed, execution)
    console.print(table)
    console.print()
    if not verbose:
        return
    for variant in result.variants:
        fingerprint = variant.fingerprint
        if fingerprint is None:
            continue
        console.print(f"{variant.variant_id} docs: sha256:{fingerprint.docs_sha256}")
        console.print(f"{variant.variant_id} benchmark: sha256:{fingerprint.overall_sha256}")
        run_id = run_ids.get(variant.variant_id)
        if run_id:
            console.print(f"{variant.variant_id} run: {run_id}")
        suite = variant.suite_result
        if suite is not None and not suite.execution_complete:
            for kind, count in suite.blocking_failure_counts().items():
                if count:
                    console.print(f"{variant.variant_id} {kind}: {count}")
    console.print()


def _render_comparability(
    console: Console,
    result: DocsExperimentRunResult,
    *,
    verbose: bool,
) -> None:
    info = experiment_comparability(result)
    model = result.requested_model or "provider default"
    console.print("Controlled benchmark components:")
    console.print("  starter: MATCH")
    console.print("  tasks: MATCH")
    console.print("  verifiers: MATCH")
    console.print(f"Requested agent/model: {result.agent_type} / {model}")
    if result.requested_model is None:
        console.print("The provider controls model resolution. AgentDocsBench kept the request constant.")
    if result.runtime_backend == "local":
        console.print("Local runtime does not fingerprint the host provider binary.")
    elif info.runtime_image_ids_differ:
        console.print("Docker image id differed between documentation variants.")
    elif info.agent_image_ids:
        console.print("Runtime: same Docker image id.")
    else:
        console.print("Docker image id: not recorded.")
    if info.resolved_model_differs:
        console.print("Resolved model differed between documentation variants.")
    elif not info.resolved_models:
        console.print("Resolved model: not reported.")
    else:
        console.print(f"Resolved model: {info.resolved_models[0]}")
    if verbose and info.resolved_model_differs:
        console.print("Resolved models: " + ", ".join(info.resolved_models))
    console.print()


def _print_capped(console: Console, label: str, paths: tuple[str, ...]) -> None:
    shown = capped_paths(paths, limit=_PATH_LIST_LIMIT)
    for path in shown:
        console.print(f"  {label}: {path}")
    remaining = len(paths) - len(shown)
    if remaining:
        console.print(f"  {label}: {remaining} more")
