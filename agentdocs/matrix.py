"""Run one benchmark against each explicit matrix target, in order."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agentdocs.agents.base import AgentAdapter, AgentError
from agentdocs.config import AgentDocsConfig, with_agent
from agentdocs.config.errors import ConfigError
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.errors import ExecutionRuntimeError
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.matrix_config import MatrixConfig
from agentdocs.matrix_progress import (
    MatrixFinished,
    MatrixProgressCallback,
    MatrixStarted,
    MatrixTargetFailed,
    MatrixTargetFinished,
    MatrixTargetStarted,
    emit_matrix_progress,
)
from agentdocs.progress import ProgressCallback
from agentdocs.suite import SuiteRunResult, run_suite
from agentdocs.verifier import VerifierError
from agentdocs.workspace import WorkspaceError

_TARGET_ERRORS = (
    AgentError,
    VerifierError,
    WorkspaceError,
    ConfigError,
    ExecutionRuntimeError,
    FileNotFoundError,
    NotADirectoryError,
)


@dataclass(frozen=True)
class MatrixTargetResult:
    """One target's suite, or the infrastructure error that stopped that target.

    A finished suite with verifier failures is completed. ``error_type`` is set
    only when AgentDocsBench could not execute the suite.
    """

    target_id: str
    agent_type: str
    requested_model: str | None
    suite_result: SuiteRunResult | None
    error_type: str | None
    error_message: str | None
    duration_seconds: float

    @property
    def completed(self) -> bool:
        return self.suite_result is not None


@dataclass(frozen=True)
class MatrixRunResult:
    """Every target from one matrix, in file order."""

    targets: tuple[MatrixTargetResult, ...]
    duration_seconds: float
    runtime_backend: str = "local"

    @property
    def completed_count(self) -> int:
        return sum(target.completed for target in self.targets)

    @property
    def error_count(self) -> int:
        return len(self.targets) - self.completed_count

    @property
    def provider_error_count(self) -> int:
        return sum(target_status(target) == "provider_error" for target in self.targets)


def run_matrix(
    benchmark_config: AgentDocsConfig,
    matrix_config: MatrixConfig,
    *,
    agent_factory: Callable[[], AgentAdapter] | None = None,
    verifier_timeout_seconds: float = 60,
    on_progress: ProgressCallback | None = None,
    on_matrix_progress: MatrixProgressCallback | None = None,
    runtime: ExecutionRuntime | None = None,
) -> MatrixRunResult:
    """Run ``benchmark_config`` once per matrix target by calling ``run_suite``.

    Each target gets a copied config. A verifier failure is stored on that
    target and the matrix continues. A known infrastructure error is stored
    and the matrix continues. Any other exception propagates.

    A returned provider failure stays on the suite result. The matrix continues
    and the child run is still available to the caller. An infrastructure
    exception still produces no suite result.
    """
    started = time.perf_counter()
    selected_runtime = runtime if runtime is not None else LocalExecutionRuntime()
    total = len(matrix_config.targets)
    emit_matrix_progress(
        on_matrix_progress,
        MatrixStarted(target_count=total, task_count=len(benchmark_config.tasks)),
    )
    results: list[MatrixTargetResult] = []
    for index, target in enumerate(matrix_config.targets, start=1):
        copied = with_agent(
            benchmark_config,
            agent_type=target.agent,
            model=target.model,
        )
        emit_matrix_progress(
            on_matrix_progress,
            MatrixTargetStarted(
                target_id=target.id,
                index=index,
                total=total,
                agent=target.agent,
                requested_model=target.model,
            ),
        )
        target_started = time.perf_counter()
        try:
            suite = run_suite(
                copied,
                agent_factory=agent_factory,
                verifier_timeout_seconds=verifier_timeout_seconds,
                on_progress=on_progress,
                runtime=selected_runtime,
            )
        except _TARGET_ERRORS as exc:
            duration = time.perf_counter() - target_started
            emit_matrix_progress(
                on_matrix_progress,
                MatrixTargetFailed(
                    target_id=target.id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_seconds=duration,
                ),
            )
            results.append(
                MatrixTargetResult(
                    target_id=target.id,
                    agent_type=target.agent,
                    requested_model=target.model,
                    suite_result=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_seconds=duration,
                )
            )
            continue
        duration = time.perf_counter() - target_started
        emit_matrix_progress(
            on_matrix_progress,
            MatrixTargetFinished(
                target_id=target.id,
                passed=suite.passed_count,
                failed=suite.failed_count,
                duration_seconds=duration,
                execution_complete=suite.execution_complete,
            ),
        )
        results.append(
            MatrixTargetResult(
                target_id=target.id,
                agent_type=target.agent,
                requested_model=target.model,
                suite_result=suite,
                error_type=None,
                error_message=None,
                duration_seconds=duration,
            )
        )
    result = MatrixRunResult(
        targets=tuple(results),
        duration_seconds=time.perf_counter() - started,
        runtime_backend=selected_runtime.backend,
    )
    emit_matrix_progress(
        on_matrix_progress,
        MatrixFinished(
            target_count=len(result.targets),
            completed=result.completed_count,
            errors=result.error_count,
            duration_seconds=result.duration_seconds,
        ),
    )
    return result


def target_status(target: MatrixTargetResult) -> str:
    """Machine status: ``pass``, ``fail``, ``provider_error``, or ``error``."""
    if target.suite_result is None:
        return "error"
    if not target.suite_result.execution_complete:
        return "provider_error"
    if target.suite_result.passed:
        return "pass"
    return "fail"


def target_status_label(target: MatrixTargetResult) -> str:
    """Terminal status. Verifier PASS/FAIL stays distinct from provider error."""
    return {
        "pass": "PASS",
        "fail": "FAIL",
        "provider_error": "PROVIDER_ERROR",
        "error": "ERROR",
    }[target_status(target)]
