"""Copy a verifier and run it against a temporary project."""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from agentdocs.config import VerifyConfig
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.verifier.errors import VerifierExecutableNotFoundError, VerifierTimeoutError
from agentdocs.verifier.parse_command import _parse_command
from agentdocs.verifier.reject_symlinks import _reject_symlinks
from agentdocs.verifier.require_directory import _require_directory
from agentdocs.verifier.require_verifier_directory import _require_verifier_directory
from agentdocs.verifier.result import VerifierResult

_DEFAULT_TIMEOUT_SECONDS = 60


def run_verifier(
    verify: VerifyConfig,
    project_dir: Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    runtime: ExecutionRuntime | None = None,
) -> VerifierResult:
    """Copy ``verify.path`` to a temp directory and run ``verify.command`` there.

    The local runtime sets ``AGENTDOCS_PROJECT_DIR`` to the absolute host
    ``project_dir``. A Docker runtime sets it to ``/workspace/project`` inside
    the verifier container. The original verifier directory is not the process
    working directory. A non-zero exit code is a failed task, not an exception.
    The temporary copy is removed when this function returns or raises.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0.")
    project_dir = _require_directory(project_dir, "project")
    _require_verifier_directory(verify.path)
    command = _parse_command(verify.command)
    _reject_symlinks(verify.path)
    selected = runtime if runtime is not None else LocalExecutionRuntime()

    with tempfile.TemporaryDirectory(prefix="agentdocs-verifier-") as directory:
        verifier_dir = Path(directory) / "verifier"
        shutil.copytree(verify.path, verifier_dir, symlinks=False)
        started = time.perf_counter()
        try:
            completed = selected.run_verifier(
                list(command),
                host_project=project_dir,
                host_verifier=verifier_dir,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            rendered = " ".join(command)
            raise VerifierTimeoutError(
                f"Verifier did not finish within {timeout_seconds:g} seconds "
                f"while running {rendered!r} against {project_dir}."
            ) from exc
        except FileNotFoundError as exc:
            raise VerifierExecutableNotFoundError(
                f"Verifier executable {command[0]!r} was not found."
            ) from exc
        duration_seconds = time.perf_counter() - started

    return VerifierResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration_seconds=duration_seconds,
    )
