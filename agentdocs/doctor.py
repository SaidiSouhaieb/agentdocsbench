"""Check whether a benchmark looks ready to run.

Doctor does not run a coding agent, a verifier, or a benchmark task. It does
not read credential files or environment values. Docker checks, when a runtime
file is supplied, only ask whether the CLI, daemon, and local images are present.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agentdocs.agents.cursor import _EXECUTABLE_NAMES as _CURSOR_EXECUTABLES
from agentdocs.config import load_config, validate_config_paths
from agentdocs.config.agentdocs_config import AgentDocsConfig
from agentdocs.config.errors import ConfigError
from agentdocs.config.require_directory import _require_directory
from agentdocs.config.require_verifier_directory import _require_verifier_directory
from agentdocs.execution.errors import RuntimeConfigError
from agentdocs.experiment_config import load_experiment_config
from agentdocs.runtime_config import load_runtime_config

_HOST_CANDIDATES = {
    "codex": ("codex",),
    "claude": ("claude",),
    "cursor": _CURSOR_EXECUTABLES,
}
DockerRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


@dataclass(frozen=True)
class DoctorCheck:
    """One diagnostic line."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class DoctorReport:
    """Every check from one doctor invocation, in display order."""

    checks: tuple[DoctorCheck, ...]

    @property
    def failed(self) -> bool:
        return any(item.status == "FAIL" for item in self.checks)


def doctor_exit_code(report: DoctorReport) -> int:
    """Return 1 when any check failed. Warnings stay at 0."""
    return 1 if report.failed else 0


def diagnose(
    config_path: str | Path = "agentdocs.yaml",
    *,
    runtime_config: str | Path | None = None,
    experiment_path: str | Path | None = None,
    which: Which | None = None,
    docker_runner: DockerRunner | None = None,
) -> DoctorReport:
    """Collect readiness checks. This function does not launch a provider CLI."""
    finder = which if which is not None else shutil.which
    run_docker = docker_runner if docker_runner is not None else _run_docker
    checks: list[DoctorCheck] = [_python_check()]
    config = _load_benchmark(config_path, checks)
    if config is None:
        checks.append(DoctorCheck("Docs directory", "SKIP", "benchmark config did not load"))
        checks.append(DoctorCheck("Starter directory", "SKIP", "benchmark config did not load"))
        checks.append(DoctorCheck("Agent", "SKIP", "benchmark config did not load"))
    else:
        _path_checks(config, checks)
        if runtime_config is None:
            _local_agent_check(config, checks, finder)
            checks.append(DoctorCheck("Runtime", "OK", "local"))
        else:
            _docker_checks(config, runtime_config, checks, finder, run_docker)
    checks.append(
        DoctorCheck(
            "Authentication",
            "SKIP",
            "provider authentication is not checked",
        )
    )
    if experiment_path is not None:
        _experiment_checks(experiment_path, checks)
    return DoctorReport(tuple(checks))


def _python_check() -> DoctorCheck:
    version = sys.version.split()[0]
    return DoctorCheck("Python 3.12+", "OK", version)


def _load_benchmark(config_path: str | Path, checks: list[DoctorCheck]) -> AgentDocsConfig | None:
    try:
        loaded = load_config(config_path)
    except (ConfigError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        checks.append(DoctorCheck("Benchmark config", "FAIL", _brief(exc)))
        return None
    checks.append(DoctorCheck("Benchmark config", "OK", str(config_path)))
    return loaded


def _path_checks(config: AgentDocsConfig, checks: list[DoctorCheck]) -> None:
    _directory_check("Docs directory", lambda: _require_directory("docs", config.docs), checks)
    if checks[-1].status == "OK" and not any(config.docs.rglob("*")):
        checks.append(DoctorCheck("Docs contents", "WARN", "docs directory has no files"))
    _directory_check(
        "Starter directory",
        lambda: _require_directory("starter", config.starter),
        checks,
    )
    for task in config.tasks:
        _directory_check(
            f"Verifier: {task.id}",
            lambda task=task: _require_verifier_directory(task),
            checks,
        )
    try:
        validate_config_paths(config)
    except (FileNotFoundError, NotADirectoryError):
        return


def _directory_check(name: str, probe, checks: list[DoctorCheck]) -> None:
    try:
        probe()
    except (FileNotFoundError, NotADirectoryError) as exc:
        checks.append(DoctorCheck(name, "FAIL", _brief(exc)))
        return
    checks.append(DoctorCheck(name, "OK"))


def _local_agent_check(
    config: AgentDocsConfig,
    checks: list[DoctorCheck],
    finder: Which,
) -> None:
    names = _HOST_CANDIDATES[config.agent.type]
    for name in names:
        found = finder(name)
        if found:
            checks.append(DoctorCheck("Agent", "OK", found))
            return
    joined = " and ".join(names)
    verb = "was" if len(names) == 1 else "were"
    checks.append(DoctorCheck("Agent", "FAIL", f"{joined} {verb} not found on PATH"))


def _docker_checks(
    config: AgentDocsConfig,
    runtime_config: str | Path,
    checks: list[DoctorCheck],
    finder: Which,
    run_docker: DockerRunner,
) -> None:
    try:
        runtime = load_runtime_config(runtime_config)
    except (RuntimeConfigError, FileNotFoundError, OSError) as exc:
        checks.append(DoctorCheck("Runtime config", "FAIL", _brief(exc)))
        checks.append(DoctorCheck("Docker executable", "SKIP", "runtime config did not load"))
        checks.append(DoctorCheck("Docker daemon", "SKIP", "runtime config did not load"))
        checks.append(DoctorCheck("Agent image", "SKIP", "runtime config did not load"))
        checks.append(DoctorCheck("Verifier image", "SKIP", "runtime config did not load"))
        checks.append(
            DoctorCheck(
                "Provider host executable",
                "SKIP",
                "Docker runtime does not require it",
            )
        )
        return
    checks.append(DoctorCheck("Runtime config", "OK", str(runtime_config)))
    try:
        agent_image = runtime.agents.for_agent(config.agent.type).image
    except RuntimeConfigError as exc:
        checks.append(DoctorCheck("Agent image", "FAIL", _brief(exc)))
        agent_image = None
    docker_path = finder("docker")
    if docker_path is None:
        checks.append(DoctorCheck("Docker executable", "FAIL", "docker was not found on PATH"))
        checks.append(DoctorCheck("Docker daemon", "SKIP", "docker executable was not found"))
        if agent_image is not None:
            checks.append(DoctorCheck("Agent image", "SKIP", "docker executable was not found"))
        checks.append(DoctorCheck("Verifier image", "SKIP", "docker executable was not found"))
    else:
        checks.append(DoctorCheck("Docker executable", "OK", docker_path))
        daemon_ok = _daemon_ok(run_docker, checks)
        if agent_image is not None:
            if daemon_ok:
                _image_check("Agent image", agent_image, run_docker, checks)
            else:
                checks.append(DoctorCheck("Agent image", "SKIP", "Docker daemon is not available"))
        if daemon_ok:
            _image_check("Verifier image", runtime.verifier.image, run_docker, checks)
        else:
            checks.append(DoctorCheck("Verifier image", "SKIP", "Docker daemon is not available"))
    checks.append(
        DoctorCheck(
            "Provider host executable",
            "SKIP",
            "Docker runtime does not require it",
        )
    )


def _daemon_ok(run_docker: DockerRunner, checks: list[DoctorCheck]) -> bool:
    completed = run_docker(["docker", "info"])
    if completed.returncode == 0:
        checks.append(DoctorCheck("Docker daemon", "OK"))
        return True
    checks.append(DoctorCheck("Docker daemon", "FAIL", "Docker daemon is not available"))
    return False


def _image_check(
    name: str,
    image: str,
    run_docker: DockerRunner,
    checks: list[DoctorCheck],
) -> None:
    completed = run_docker(["docker", "image", "inspect", image])
    if completed.returncode == 0:
        checks.append(DoctorCheck(name, "OK", image))
        return
    checks.append(DoctorCheck(name, "FAIL", f"{image} is not available locally"))


def _experiment_checks(experiment_path: str | Path, checks: list[DoctorCheck]) -> None:
    try:
        loaded = load_experiment_config(experiment_path)
    except (ConfigError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        checks.append(DoctorCheck("Experiment", "FAIL", _brief(exc)))
        checks.append(DoctorCheck("Reference", "SKIP", "experiment config did not load"))
        checks.append(DoctorCheck("Variants", "SKIP", "experiment config did not load"))
        return
    checks.append(DoctorCheck("Experiment", "OK", str(experiment_path)))
    checks.append(DoctorCheck("Reference", "OK", loaded.reference))
    checks.append(DoctorCheck("Variants", "OK", str(len(loaded.variants))))


def _run_docker(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )


def _brief(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:240]
