"""Lifecycle events for one benchmark run.

These objects describe what is happening. They do not print anything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Union

from agentdocs.agents.base import AgentRunResult
from agentdocs.verifier import VerifierResult


@dataclass(frozen=True)
class SuiteStarted:
    total_tasks: int


@dataclass(frozen=True)
class TaskStarted:
    task_id: str
    index: int
    total: int


@dataclass(frozen=True)
class AgentStarted:
    task_id: str
    agent: str
    requested_model: str | None = None


@dataclass(frozen=True)
class AgentFinished:
    """The agent process returned. ``result.exit_code`` is not a task pass."""

    task_id: str
    result: AgentRunResult


@dataclass(frozen=True)
class WorkspaceChangesCaptured:
    """Project edits observed after the agent returned and before verification.

    Counts only. The full manifest stays on the task result.
    """

    task_id: str
    files_added: int
    files_modified: int
    files_deleted: int
    directories_added: int
    directories_deleted: int
    type_changed: int
    paths: tuple[str, ...] = ()
    paths_omitted: int = 0


@dataclass(frozen=True)
class AgentFailed:
    """The agent raised before a result existed. The exception still propagates."""

    task_id: str
    agent: str
    error: str
    duration_seconds: float


@dataclass(frozen=True)
class VerifierStarted:
    task_id: str


@dataclass(frozen=True)
class VerifierFinished:
    task_id: str
    result: VerifierResult


@dataclass(frozen=True)
class VerifierFailed:
    """The verifier raised. The exception still propagates."""

    task_id: str
    error: str
    duration_seconds: float


@dataclass(frozen=True)
class TaskFinished:
    task_id: str
    passed: bool
    duration_seconds: float


@dataclass(frozen=True)
class SuiteFinished:
    total: int
    passed: int
    failed: int
    duration_seconds: float


ProgressEvent = Union[
    SuiteStarted,
    TaskStarted,
    AgentStarted,
    AgentFinished,
    WorkspaceChangesCaptured,
    AgentFailed,
    VerifierStarted,
    VerifierFinished,
    VerifierFailed,
    TaskFinished,
    SuiteFinished,
]
ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """Send ``event`` to ``callback``. A missing callback is ignored.

    If the callback raises, the exception propagates. Progress reporting does
    not swallow caller bugs.
    """
    if callback is not None:
        callback(event)
