"""Execution runtime contract.

Local and Docker runtimes both expose visible project and docs paths, run
agent and verifier commands, and report provenance. Adapters call this
contract. They do not branch on the backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import subprocess

from agentdocs.execution.runtime_info import RuntimeInfo


class ExecutionRuntime(Protocol):
    """Host-process or container execution for one benchmark invocation."""

    backend: str

    def prepare(self, agent_type: str) -> None:
        """Validate this agent type and cache image metadata. Local is a no-op."""

    def agent_visible_paths(self, project_dir: Path, docs_dir: Path) -> tuple[str, str]:
        """Return the project and docs paths the agent process can see."""

    def resolve_agent_executable(
        self,
        candidates: tuple[str, ...],
        *,
        missing_message: str,
        agent_type: str,
    ) -> str:
        """Return the executable name or path for ``candidates``, in order."""

    def run_agent(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_docs: Path,
        timeout_seconds: float,
        agent_type: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the provider command. A normal exit code is returned as-is."""

    def run_verifier(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_verifier: Path,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run the verifier command. A normal exit code is returned as-is."""

    def provenance(self, agent_type: str) -> RuntimeInfo:
        """Return the runtime record for a completed suite."""

    def status_lines(self, agent_type: str, *, verbose: bool) -> list[str]:
        """Header lines. These omit mount paths, env values, and container ids."""
