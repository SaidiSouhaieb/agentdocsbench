"""Run the agent and verifier as host processes.

This is the default runtime. It does not look up or invoke Docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agentdocs.agents.base import AgentExecutableNotFoundError
from agentdocs.execution.runtime_info import RuntimeInfo, local_runtime_info


class LocalExecutionRuntime:
    """Current host subprocess behavior."""

    backend = "local"

    def prepare(self, agent_type: str) -> None:
        """Local execution has no image or daemon check."""
        del agent_type

    def agent_visible_paths(self, project_dir: Path, docs_dir: Path) -> tuple[str, str]:
        """The agent sees the real temporary workspace paths."""
        return str(project_dir.resolve()), str(docs_dir.resolve())

    def resolve_agent_executable(
        self,
        candidates: tuple[str, ...],
        *,
        missing_message: str,
        agent_type: str,
    ) -> str:
        """Find the first candidate on the host PATH."""
        del agent_type
        for name in candidates:
            found = shutil.which(name)
            if found is not None:
                return found
        raise AgentExecutableNotFoundError(missing_message)

    def run_agent(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_docs: Path,
        timeout_seconds: float,
        agent_type: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the provider CLI in ``host_project`` with the inherited environment."""
        del host_docs, agent_type
        return subprocess.run(
            command,
            cwd=host_project,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )

    def run_verifier(
        self,
        command: list[str],
        *,
        host_project: Path,
        host_verifier: Path,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run the verifier on the host. The project path is the host workspace."""
        environment = os.environ.copy()
        environment["AGENTDOCS_PROJECT_DIR"] = str(host_project.resolve())
        return subprocess.run(
            command,
            cwd=host_verifier,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
            shell=False,
            check=False,
        )

    def provenance(self, agent_type: str) -> RuntimeInfo:
        """Local runs record only the backend."""
        del agent_type
        return local_runtime_info()

    def status_lines(self, agent_type: str, *, verbose: bool) -> list[str]:
        """One header line. Verbose names the backend and does not mention Docker."""
        del agent_type
        lines = ["Runtime: local"]
        if verbose:
            lines.append("Runtime backend: local")
        return lines
