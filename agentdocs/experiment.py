"""Run one benchmark against each documentation variant.

The experimental variable is the docs tree. Starter, tasks, prompts, verifier
commands, verifier source, agent, requested model, and runtime stay on the
base config and the runtime object the caller supplied. Each completed
variant is an ordinary suite run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agentdocs.agents.base import AgentAdapter, AgentError
from agentdocs.config import AgentDocsConfig, ConfigError
from agentdocs.config.with_docs import with_docs
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.errors import ExecutionRuntimeError
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.execution.runtime_info import RuntimeInfo
from agentdocs.experiment_config import DocsExperimentConfig
from agentdocs.experiment_progress import (
    ExperimentFinished,
    ExperimentProgressCallback,
    ExperimentStarted,
    VariantError,
    VariantFinished,
    VariantStarted,
    emit_experiment_progress,
)
from agentdocs.fingerprint import (
    BenchmarkFingerprint,
    BenchmarkFingerprintError,
    TreeChangeManifest,
    compare_benchmark_fingerprints,
    compute_benchmark_fingerprint,
)
from agentdocs.progress import ProgressCallback
from agentdocs.runner import TaskRunResult
from agentdocs.suite import SuiteRunResult, run_suite
from agentdocs.verifier import VerifierError
from agentdocs.workspace import WorkspaceError

TRANSITIONS = (
    "unchanged_pass",
    "unchanged_fail",
    "newly_passing",
    "newly_failing",
    "not_comparable",
)
_VARIANT_ERRORS = (
    AgentError,
    VerifierError,
    WorkspaceError,
    ConfigError,
    ExecutionRuntimeError,
    BenchmarkFingerprintError,
    FileNotFoundError,
    NotADirectoryError,
)
VariantObserver = Callable[["DocsVariantRunResult", AgentDocsConfig], None]


class ExperimentInvariantError(Exception):
    """A variant changed a benchmark input other than documentation.

    The experiment stops. A docs-only comparison is not reported for a run
    whose starter, tasks, or verifiers differ from the reference fingerprint.
    """


@dataclass(frozen=True)
class DocsVariantRunResult:
    """One variant's suite, or the infrastructure error that stopped it."""

    variant_id: str
    position: int
    fingerprint: BenchmarkFingerprint | None
    suite_result: SuiteRunResult | None
    error_type: str | None
    error_message: str | None
    duration_seconds: float

    @property
    def status(self) -> str:
        return variant_status(self)


@dataclass(frozen=True)
class TaskTransition:
    """One task's observed verifier outcomes relative to the reference."""

    task_id: str
    reference_result: str | None
    variant_result: str | None
    transition: str


@dataclass(frozen=True)
class VariantComparison:
    """One non-reference variant compared only with the reference."""

    variant_id: str
    reference_variant: str
    docs_changes: TreeChangeManifest | None
    tasks: tuple[TaskTransition, ...]


@dataclass(frozen=True)
class DocsExperimentRunResult:
    """Every documentation variant from one experiment, in file order."""

    reference_variant: str
    agent_type: str
    requested_model: str | None
    task_ids: tuple[str, ...]
    variants: tuple[DocsVariantRunResult, ...]
    comparisons: tuple[VariantComparison, ...]
    duration_seconds: float
    runtime_backend: str
    controlled_starter_sha256: str
    controlled_tasks_sha256: str
    controlled_verifiers_sha256: str
    reference_benchmark_sha256: str


@dataclass(frozen=True)
class ExperimentComparability:
    """What the experiment could and could not hold constant."""

    resolved_models: tuple[str, ...]
    resolved_model_differs: bool
    agent_image_ids: tuple[str, ...]
    verifier_image_ids: tuple[str, ...]
    runtime_image_ids_differ: bool


def run_docs_experiment(
    base_config: AgentDocsConfig,
    experiment_config: DocsExperimentConfig,
    *,
    agent_factory: Callable[[], AgentAdapter] | None = None,
    verifier_timeout_seconds: float = 60,
    on_progress: ProgressCallback | None = None,
    on_experiment_progress: ExperimentProgressCallback | None = None,
    runtime: ExecutionRuntime | None = None,
    on_variant_complete: VariantObserver | None = None,
) -> DocsExperimentRunResult:
    """Run ``base_config`` once per docs variant by calling ``run_suite``.

    Variants run in file order. The reference variant is not moved to the
    front. ``on_variant_complete`` runs after a variant's suite returns,
    including a blocking provider failure, and before the next variant. An
    infrastructure exception is stored and later variants continue.
    ``ExperimentInvariantError`` and any other unexpected exception propagate.
    """
    started = time.perf_counter()
    selected_runtime = runtime if runtime is not None else LocalExecutionRuntime()
    reference = _reference_variant(experiment_config)
    reference_config = with_docs(base_config, reference.docs)
    reference_fingerprint = compute_benchmark_fingerprint(reference_config)
    task_ids = tuple(task.id for task in base_config.tasks)
    emit_experiment_progress(
        on_experiment_progress,
        ExperimentStarted(
            variant_count=len(experiment_config.variants),
            task_count=len(task_ids),
            reference_variant=experiment_config.reference,
        ),
    )
    recorded: list[DocsVariantRunResult] = []
    total = len(experiment_config.variants)
    for index, variant in enumerate(experiment_config.variants):
        copied = with_docs(base_config, variant.docs)
        emit_experiment_progress(
            on_experiment_progress,
            VariantStarted(variant_id=variant.id, index=index + 1, total=total),
        )
        variant_started = time.perf_counter()
        fingerprint: BenchmarkFingerprint | None = None
        try:
            fingerprint = compute_benchmark_fingerprint(copied)
            _require_controlled_inputs(reference_fingerprint, fingerprint, variant.id)
            suite = run_suite(
                copied,
                agent_factory=agent_factory,
                verifier_timeout_seconds=verifier_timeout_seconds,
                on_progress=on_progress,
                runtime=selected_runtime,
            )
        except ExperimentInvariantError:
            raise
        except _VARIANT_ERRORS as exc:
            duration = time.perf_counter() - variant_started
            emit_experiment_progress(
                on_experiment_progress,
                VariantError(
                    variant_id=variant.id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_seconds=duration,
                ),
            )
            recorded.append(
                DocsVariantRunResult(
                    variant_id=variant.id,
                    position=index,
                    fingerprint=fingerprint,
                    suite_result=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_seconds=duration,
                )
            )
            continue
        duration = time.perf_counter() - variant_started
        variant_result = DocsVariantRunResult(
            variant_id=variant.id,
            position=index,
            fingerprint=fingerprint,
            suite_result=suite,
            error_type=None,
            error_message=None,
            duration_seconds=duration,
        )
        recorded.append(variant_result)
        emit_experiment_progress(
            on_experiment_progress,
            VariantFinished(
                variant_id=variant.id,
                status=variant_result.status,
                passed=suite.passed_count,
                failed=suite.failed_count,
                duration_seconds=duration,
            ),
        )
        if on_variant_complete is not None:
            on_variant_complete(variant_result, copied)
    variants = tuple(recorded)
    result = DocsExperimentRunResult(
        reference_variant=experiment_config.reference,
        agent_type=base_config.agent.type,
        requested_model=base_config.agent.model,
        task_ids=task_ids,
        variants=variants,
        comparisons=_comparisons(
            variants,
            reference_variant=experiment_config.reference,
            task_ids=task_ids,
        ),
        duration_seconds=time.perf_counter() - started,
        runtime_backend=selected_runtime.backend,
        controlled_starter_sha256=reference_fingerprint.starter_sha256,
        controlled_tasks_sha256=reference_fingerprint.tasks_sha256,
        controlled_verifiers_sha256=reference_fingerprint.verifiers_sha256,
        reference_benchmark_sha256=reference_fingerprint.overall_sha256,
    )
    emit_experiment_progress(
        on_experiment_progress,
        ExperimentFinished(
            variant_count=len(result.variants),
            duration_seconds=result.duration_seconds,
        ),
    )
    return result


def variant_status(variant: DocsVariantRunResult) -> str:
    """``pass``, ``fail``, ``provider_error``, or ``error``."""
    suite = variant.suite_result
    if suite is None:
        return "error"
    if not suite.execution_complete:
        return "provider_error"
    if suite.passed:
        return "pass"
    return "fail"


def variant_status_label(variant: DocsVariantRunResult) -> str:
    return {
        "pass": "PASS",
        "fail": "FAIL",
        "provider_error": "PROVIDER_ERROR",
        "error": "ERROR",
    }[variant_status(variant)]


def experiment_status(result: DocsExperimentRunResult) -> str:
    """``error`` outranks ``provider_error``, which outranks ``complete``."""
    statuses = [variant.status for variant in result.variants]
    if "error" in statuses:
        return "error"
    if "provider_error" in statuses:
        return "provider_error"
    return "complete"


def experiment_exit_code(result: DocsExperimentRunResult) -> int:
    """Return 0 when every variant suite completed, otherwise 2.

    Verifier pass and fail, including a newly failing task, stay inside the
    report. They do not change this exit code. A blocking provider failure or
    an infrastructure error means the experiment is incomplete.
    """
    if experiment_status(result) == "complete":
        return 0
    return 2


def aggregate_transition_counts(result: DocsExperimentRunResult) -> dict[str, int]:
    """Count transitions across every non-reference variant versus the reference."""
    counts = {name: 0 for name in TRANSITIONS}
    for comparison in result.comparisons:
        for task in comparison.tasks:
            counts[task.transition] += 1
    return counts


def experiment_comparability(result: DocsExperimentRunResult) -> ExperimentComparability:
    """Resolved models and Docker image ids observed on completed variants."""
    models: list[str] = []
    agent_images: list[str] = []
    verifier_images: list[str] = []
    for variant in result.variants:
        suite = variant.suite_result
        if suite is None:
            continue
        for task in suite.results:
            resolved = task.agent_result.resolved_model
            if resolved and resolved not in models:
                models.append(resolved)
        _collect_image_id(agent_images, suite.runtime, agent=True)
        _collect_image_id(verifier_images, suite.runtime, agent=False)
    return ExperimentComparability(
        resolved_models=tuple(models),
        resolved_model_differs=len(models) > 1,
        agent_image_ids=tuple(agent_images),
        verifier_image_ids=tuple(verifier_images),
        runtime_image_ids_differ=len(agent_images) > 1 or len(verifier_images) > 1,
    )


def _reference_variant(experiment_config: DocsExperimentConfig):
    for variant in experiment_config.variants:
        if variant.id == experiment_config.reference:
            return variant
    raise ExperimentInvariantError(
        f"Reference {experiment_config.reference!r} must match exactly one variant id."
    )


def _require_controlled_inputs(
    reference: BenchmarkFingerprint,
    variant: BenchmarkFingerprint,
    variant_id: str,
) -> None:
    mismatched = []
    if reference.starter_sha256 != variant.starter_sha256:
        mismatched.append("starter")
    if reference.tasks_sha256 != variant.tasks_sha256:
        mismatched.append("tasks")
    if reference.verifiers_sha256 != variant.verifiers_sha256:
        mismatched.append("verifiers")
    if mismatched:
        joined = ", ".join(mismatched)
        raise ExperimentInvariantError(
            f"Variant {variant_id!r} changed {joined}. "
            "A documentation experiment can change only the docs tree."
        )


def _comparisons(
    variants: tuple[DocsVariantRunResult, ...],
    *,
    reference_variant: str,
    task_ids: tuple[str, ...],
) -> tuple[VariantComparison, ...]:
    reference = next(variant for variant in variants if variant.variant_id == reference_variant)
    comparisons: list[VariantComparison] = []
    for variant in variants:
        if variant.variant_id == reference_variant:
            continue
        docs_changes = None
        if reference.fingerprint is not None and variant.fingerprint is not None:
            docs_changes = compare_benchmark_fingerprints(
                reference.fingerprint,
                variant.fingerprint,
            ).docs
        comparisons.append(
            VariantComparison(
                variant_id=variant.variant_id,
                reference_variant=reference_variant,
                docs_changes=docs_changes,
                tasks=tuple(
                    _task_transition(task_id, reference, variant) for task_id in task_ids
                ),
            )
        )
    return tuple(comparisons)


def _task_transition(
    task_id: str,
    reference: DocsVariantRunResult,
    variant: DocsVariantRunResult,
) -> TaskTransition:
    reference_task = _task_from_suite(reference.suite_result, task_id)
    variant_task = _task_from_suite(variant.suite_result, task_id)
    reference_label = _verifier_label(reference_task)
    variant_label = _verifier_label(variant_task)
    if reference_task is None or variant_task is None:
        return TaskTransition(task_id, reference_label, variant_label, "not_comparable")
    if _blocking(reference_task) or _blocking(variant_task):
        return TaskTransition(task_id, reference_label, variant_label, "not_comparable")
    if reference_task.passed and variant_task.passed:
        transition = "unchanged_pass"
    elif not reference_task.passed and not variant_task.passed:
        transition = "unchanged_fail"
    elif variant_task.passed:
        transition = "newly_passing"
    else:
        transition = "newly_failing"
    return TaskTransition(task_id, reference_label, variant_label, transition)


def _task_from_suite(suite: SuiteRunResult | None, task_id: str) -> TaskRunResult | None:
    if suite is None:
        return None
    for task in suite.results:
        if task.task_id == task_id:
            return task
    return None


def _verifier_label(task: TaskRunResult | None) -> str | None:
    if task is None:
        return None
    return "PASS" if task.passed else "FAIL"


def _blocking(task: TaskRunResult) -> bool:
    failure = task.agent_result.failure
    return failure is not None and failure.blocking


def _collect_image_id(found: list[str], runtime: RuntimeInfo, *, agent: bool) -> None:
    info = runtime.agent if agent else runtime.verifier
    if info is None or not info.image_id or info.image_id in found:
        return
    found.append(info.image_id)
