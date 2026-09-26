"""Lifecycle events for one documentation experiment.

These objects name the active variant. They do not print anything. Task
events stay on the existing suite progress callback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class ExperimentStarted:
    variant_count: int
    task_count: int
    reference_variant: str


@dataclass(frozen=True)
class VariantStarted:
    variant_id: str
    index: int
    total: int


@dataclass(frozen=True)
class VariantFinished:
    variant_id: str
    status: str
    passed: int
    failed: int
    duration_seconds: float


@dataclass(frozen=True)
class VariantError:
    variant_id: str
    error_type: str
    error_message: str
    duration_seconds: float


@dataclass(frozen=True)
class ExperimentFinished:
    variant_count: int
    duration_seconds: float


ExperimentProgressEvent = Union[
    ExperimentStarted,
    VariantStarted,
    VariantFinished,
    VariantError,
    ExperimentFinished,
]
ExperimentProgressCallback = Callable[[ExperimentProgressEvent], None]


def emit_experiment_progress(
    callback: ExperimentProgressCallback | None,
    event: ExperimentProgressEvent,
) -> None:
    """Send ``event`` to ``callback``. A missing callback is ignored.

    If the callback raises, the exception propagates.
    """
    if callback is not None:
        callback(event)
