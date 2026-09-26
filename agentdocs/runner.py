"""Run one benchmark task from workspace creation through verification."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agentdocs.agents.base import AgentAdapter, AgentRunResult
from agentdocs.agents.factory import create_agent_adapter
from agentdocs.config import AgentDocsConfig, TaskConfig
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.progress import (
    AgentFailed,
    AgentFinished,
    AgentStarted,
    ProgressCallback,
    TaskFinished,
    WorkspaceChangesCaptured,
    VerifierFailed,
    VerifierFinished,
    VerifierStarted,
    emit_progress,
)
from agentdocs.verifier import VerifierResult, run_verifier
from agentdocs.workspace import create_workspace
from agentdocs.workspace_changes import (
    WorkspaceChangeManifest,
    capped_change_paths,
    capture_workspace_snapshot,
    compare_workspace_snapshots,
    empty_workspace_changes,
)


@dataclass(frozen=True)
class TaskRunResult:
    """One task's agent process result and verifier result.

    ``passed`` follows the verifier only. ``agent_completed_cleanly`` follows
    the agent process exit code. Those are different questions.
    """

    task_id: str
    agent_result: AgentRunResult
    verifier_result: VerifierResult
    duration_seconds: float
    workspace_changes: WorkspaceChangeManifest = field(default_factory=empty_workspace_changes)

    @property
    def passed(self) -> bool:
        return self.verifier_result.passed

    @property
    def agent_completed_cleanly(self) -> bool:
        return self.agent_result.exit_code == 0


def run_task(
    config: AgentDocsConfig,
    task: TaskConfig,
    *,
    agent: AgentAdapter | None = None,
    verifier_timeout_seconds: float = 60,
    on_progress: ProgressCallback | None = None,
    runtime: ExecutionRuntime | None = None,
) -> TaskRunResult:
    """Run ``task`` in a fresh workspace and return both process results.

    The workspace is deleted before this function returns, including when the
    agent or verifier raises. A non-zero agent exit code still continues to
    the verifier. Infrastructure exceptions propagate and do not become
    ``passed=False``.
    """
    if verifier_timeout_seconds <= 0:
        raise ValueError("verifier_timeout_seconds must be greater than 0.")
    _require_task(config, task)

    started = time.perf_counter()
    selected_runtime = runtime if runtime is not None else LocalExecutionRuntime()
    selected_runtime.prepare(config.agent.type)
    selected = (
        agent
        if agent is not None
        else create_agent_adapter(
            config.agent.type,
            model=config.agent.model,
            runtime=selected_runtime,
        )
    )
    with create_workspace(config) as workspace:
        before = capture_workspace_snapshot(workspace.project)
        agent_started = time.perf_counter()
        emit_progress(
            on_progress,
            AgentStarted(
                task_id=task.id,
                agent=config.agent.type,
                requested_model=config.agent.model,
            ),
        )
        try:
            agent_result = selected.run(
                prompt=task.prompt,
                project_dir=workspace.project,
                docs_dir=workspace.docs,
            )
        except Exception as exc:
            emit_progress(
                on_progress,
                AgentFailed(
                    task_id=task.id,
                    agent=config.agent.type,
                    error=str(exc),
                    duration_seconds=time.perf_counter() - agent_started,
                ),
            )
            raise
        emit_progress(on_progress, AgentFinished(task_id=task.id, result=agent_result))
        after = capture_workspace_snapshot(workspace.project)
        changes = compare_workspace_snapshots(before, after)
        paths, omitted = capped_change_paths(changes)
        emit_progress(
            on_progress,
            WorkspaceChangesCaptured(
                task_id=task.id,
                files_added=len(changes.added_files),
                files_modified=len(changes.modified_files),
                files_deleted=len(changes.deleted_files),
                directories_added=len(changes.added_directories),
                directories_deleted=len(changes.deleted_directories),
                type_changed=len(changes.type_changes),
                paths=paths,
                paths_omitted=omitted,
            ),
        )

        verifier_started = time.perf_counter()
        emit_progress(on_progress, VerifierStarted(task_id=task.id))
        try:
            verifier_result = run_verifier(
                task.verify,
                workspace.project,
                timeout_seconds=verifier_timeout_seconds,
                runtime=selected_runtime,
            )
        except Exception as exc:
            emit_progress(
                on_progress,
                VerifierFailed(
                    task_id=task.id,
                    error=str(exc),
                    duration_seconds=time.perf_counter() - verifier_started,
                ),
            )
            raise
        emit_progress(
            on_progress,
            VerifierFinished(task_id=task.id, result=verifier_result),
        )
        duration_seconds = time.perf_counter() - started
        result = TaskRunResult(
            task_id=task.id,
            agent_result=agent_result,
            verifier_result=verifier_result,
            duration_seconds=duration_seconds,
            workspace_changes=changes,
        )
        emit_progress(
            on_progress,
            TaskFinished(
                task_id=task.id,
                passed=result.passed,
                duration_seconds=duration_seconds,
            ),
        )
        return result


def _require_task(config: AgentDocsConfig, task: TaskConfig) -> None:
    if any(item.id == task.id for item in config.tasks):
        return
    raise ValueError(
        f"Task {task.id!r} is not present in the supplied AgentDocsConfig."
    )
