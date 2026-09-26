"""Run every task in an AgentDocsBench config, one at a time."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentdocs.agents.base import AgentAdapter
from agentdocs.agents.failures import BLOCKING_FAILURE_KINDS, AgentFailureKind
from agentdocs.config import AgentDocsConfig
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.execution.runtime_info import RuntimeInfo, local_runtime_info
from agentdocs.progress import (
    ProgressCallback,
    SuiteFinished,
    SuiteStarted,
    TaskStarted,
    emit_progress,
)
from agentdocs.runner import TaskRunResult, run_task


@dataclass(frozen=True)
class SuiteRunResult:
    """Aggregate of one sequential pass over ``config.tasks``.

    ``passed`` is true only when every task's verifier passed. Agent process
    exit codes are available on each ``TaskRunResult`` and do not replace that.
    """

    results: tuple[TaskRunResult, ...]
    duration_seconds: float
    runtime: RuntimeInfo = field(default_factory=local_runtime_info)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def blocking_agent_failure_count(self) -> int:
        """Tasks whose returned agent process had a known blocking provider failure."""
        return sum(
            result.agent_result.failure is not None and result.agent_result.failure.blocking
            for result in self.results
        )

    @property
    def unknown_agent_failure_count(self) -> int:
        """Non-zero agent exits that did not match a known provider rule."""
        return sum(
            result.agent_result.failure is not None
            and result.agent_result.failure.kind is AgentFailureKind.unknown_agent_failure
            for result in self.results
        )

    @property
    def execution_complete(self) -> bool:
        """True when no task recorded a blocking provider failure."""
        return self.blocking_agent_failure_count == 0

    def blocking_failure_counts(self) -> dict[str, int]:
        """Counts for the blocking kinds, in taxonomy order. Unknown exits are omitted."""
        counts = {kind.value: 0 for kind in BLOCKING_FAILURE_KINDS}
        for result in self.results:
            failure = result.agent_result.failure
            if failure is None or not failure.blocking:
                continue
            counts[failure.kind.value] += 1
        return counts


def run_suite(
    config: AgentDocsConfig,
    *,
    agent_factory: Callable[[], AgentAdapter] | None = None,
    verifier_timeout_seconds: float = 60,
    on_progress: ProgressCallback | None = None,
    runtime: ExecutionRuntime | None = None,
) -> SuiteRunResult:
    """Run each task with :func:`run_task`, in config order.

    A verifier failure is stored and the suite continues. An exception from
    ``run_task`` propagates and later tasks are not started. Each ``run_task``
    call builds its own workspace. When ``agent_factory`` is set, it is called
    once per task so adapters are not reused.
    """
    if verifier_timeout_seconds <= 0:
        raise ValueError("verifier_timeout_seconds must be greater than 0.")

    started = time.perf_counter()
    selected_runtime = runtime if runtime is not None else LocalExecutionRuntime()
    total = len(config.tasks)
    emit_progress(on_progress, SuiteStarted(total_tasks=total))
    results: list[TaskRunResult] = []
    for index, task in enumerate(config.tasks, start=1):
        emit_progress(
            on_progress,
            TaskStarted(task_id=task.id, index=index, total=total),
        )
        agent = agent_factory() if agent_factory is not None else None
        results.append(
            run_task(
                config,
                task,
                agent=agent,
                verifier_timeout_seconds=verifier_timeout_seconds,
                on_progress=on_progress,
                runtime=selected_runtime,
            )
        )
    result = SuiteRunResult(
        results=tuple(results),
        duration_seconds=time.perf_counter() - started,
        runtime=selected_runtime.provenance(config.agent.type),
    )
    emit_progress(
        on_progress,
        SuiteFinished(
            total=result.total,
            passed=result.passed_count,
            failed=result.failed_count,
            duration_seconds=result.duration_seconds,
        ),
    )
    return result
