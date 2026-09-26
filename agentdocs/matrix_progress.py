"""Lifecycle events for one matrix run.

These objects describe which target is active. They do not print anything.
Task events stay on the existing progress callback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class MatrixStarted:
    target_count: int
    task_count: int


@dataclass(frozen=True)
class MatrixTargetStarted:
    target_id: str
    index: int
    total: int
    agent: str
    requested_model: str | None


@dataclass(frozen=True)
class MatrixTargetFinished:
    target_id: str
    passed: int
    failed: int
    duration_seconds: float
    execution_complete: bool = True


@dataclass(frozen=True)
class MatrixTargetFailed:
    target_id: str
    error_type: str
    error_message: str
    duration_seconds: float


@dataclass(frozen=True)
class MatrixFinished:
    target_count: int
    completed: int
    errors: int
    duration_seconds: float


MatrixProgressEvent = Union[
    MatrixStarted,
    MatrixTargetStarted,
    MatrixTargetFinished,
    MatrixTargetFailed,
    MatrixFinished,
]
MatrixProgressCallback = Callable[[MatrixProgressEvent], None]


def emit_matrix_progress(
    callback: MatrixProgressCallback | None,
    event: MatrixProgressEvent,
) -> None:
    """Send ``event`` to ``callback``. A missing callback is ignored.

    If the callback raises, the exception propagates.
    """
    if callback is not None:
        callback(event)
