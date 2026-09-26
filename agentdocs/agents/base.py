"""Shared types for coding-agent adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentdocs.agents.failures import AgentFailureClassification


class AgentError(RuntimeError):
    """Raised when AgentDocsBench cannot complete an agent run."""


class AgentExecutableNotFoundError(AgentError):
    """Raised when the coding-agent executable is not on PATH."""


class AgentTimeoutError(AgentError):
    """Raised when an agent process exceeds its time limit."""


class AgentOutputError(AgentError):
    """Raised when an agent process returns output AgentDocsBench cannot parse."""


@dataclass(frozen=True)
class AgentRunResult:
    """What one coding-agent process did. This is not a benchmark pass or fail.

    ``requested_model`` is the id AgentDocsBench asked the CLI to use.
    ``None`` means the CLI default was left in place. ``resolved_model`` is
    the model the provider reported back. It stays ``None`` when that report
    is missing or not trustworthy.
    """

    agent: str
    exit_code: int
    events: tuple[dict[str, Any], ...]
    stdout: str
    stderr: str
    duration_seconds: float
    requested_model: str | None = None
    resolved_model: str | None = None
    failure: AgentFailureClassification | None = None


class AgentAdapter(ABC):
    """Runs one coding agent against a project directory and a docs directory."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        project_dir: Path,
        docs_dir: Path,
    ) -> AgentRunResult:
        """Run the agent and return its process result."""
