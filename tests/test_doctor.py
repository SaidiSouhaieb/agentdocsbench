"""Doctor diagnostics. Docker and provider CLIs are mocked. Nothing is launched."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs.cli import app
from agentdocs.doctor import DoctorCheck, diagnose, doctor_exit_code

runner = CliRunner()


def _benchmark(root: Path, *, agent: str = "codex", docs_file: bool = True) -> Path:
    docs = root / "docs"
    docs.mkdir()
    if docs_file:
        (docs / "guide.md").write_text("hello\n", encoding="utf-8")
    (root / "starter").mkdir()
    (root / "starter" / "README.md").write_text("starter\n", encoding="utf-8")
    verifier = root / "verifiers" / "first_task"
    verifier.mkdir(parents=True)
    (verifier / "check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    config = root / "agentdocs.yaml"
    config.write_text(
        "version: 1\n"
        "docs: ./docs\n"
        "starter: ./starter\n"
        "agent:\n"
        f"  type: {agent}\n"
        "tasks:\n"
        "  - id: first_task\n"
        "    prompt: Do the task.\n"
        "    verify:\n"
        "      path: ./verifiers/first_task\n"
        "      command: python3 check.py\n",
        encoding="utf-8",
    )
    return config


def _which(mapping: dict[str, str | None]):
    calls: list[str] = []

    def which(name: str) -> str | None:
        calls.append(name)
        return mapping.get(name)

    which.calls = calls  # type: ignore[attr-defined]
    return which


def _status(report, name: str) -> DoctorCheck:
    matches = [item for item in report.checks if item.name == name]
    assert matches, name
    return matches[-1]


def _completed(code: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout="", stderr="")


def test_healthy_local_config(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    which = _which({"codex": "/usr/bin/codex"})
    report = diagnose(config, which=which)
    assert _status(report, "Python 3.12+").status == "OK"
    assert _status(report, "Benchmark config").status == "OK"
    assert _status(report, "Docs directory").status == "OK"
    assert _status(report, "Starter directory").status == "OK"
    assert _status(report, "Verifier: first_task").status == "OK"
    assert _status(report, "Agent").status == "OK"
    assert _status(report, "Agent").detail == "/usr/bin/codex"
    assert _status(report, "Runtime").detail == "local"
    assert doctor_exit_code(report) == 0


def test_missing_config_skips_dependents(tmp_path: Path) -> None:
    report = diagnose(tmp_path / "missing.yaml", which=_which({}))
    assert _status(report, "Benchmark config").status == "FAIL"
    assert "Config file not found" in _status(report, "Benchmark config").detail
    assert _status(report, "Docs directory").status == "SKIP"
    assert _status(report, "Starter directory").status == "SKIP"
    assert _status(report, "Agent").status == "SKIP"
    assert doctor_exit_code(report) == 1


def test_invalid_config_does_not_crash(tmp_path: Path) -> None:
    config = tmp_path / "agentdocs.yaml"
    config.write_text("version: 99\n", encoding="utf-8")
    report = diagnose(config, which=_which({}))
    assert _status(report, "Benchmark config").status == "FAIL"
    assert _status(report, "Docs directory").status == "SKIP"


def test_missing_docs_starter_and_verifier(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    (tmp_path / "docs").rename(tmp_path / "docs-gone")
    (tmp_path / "starter").rename(tmp_path / "starter-gone")
    (tmp_path / "verifiers").rename(tmp_path / "verifiers-gone")
    report = diagnose(config, which=_which({"codex": "/usr/bin/codex"}))
    assert _status(report, "Docs directory").status == "FAIL"
    assert _status(report, "Starter directory").status == "FAIL"
    assert _status(report, "Verifier: first_task").status == "FAIL"
    assert doctor_exit_code(report) == 1


@pytest.mark.parametrize(
    ("agent", "present", "expected"),
    [
        ("codex", True, "OK"),
        ("codex", False, "FAIL"),
        ("claude", True, "OK"),
        ("claude", False, "FAIL"),
    ],
)
def test_codex_and_claude_executables(tmp_path: Path, agent: str, present: bool, expected: str) -> None:
    config = _benchmark(tmp_path, agent=agent)
    found = f"/usr/bin/{agent}" if present else None
    report = diagnose(config, which=_which({agent: found}))
    assert _status(report, "Agent").status == expected
    if not present:
        assert agent in _status(report, "Agent").detail


def test_cursor_prefers_agent_then_cursor_agent(tmp_path: Path) -> None:
    config = _benchmark(tmp_path, agent="cursor")
    preferred = _which({"agent": "/usr/bin/agent", "cursor-agent": "/usr/bin/cursor-agent"})
    report = diagnose(config, which=preferred)
    assert _status(report, "Agent").detail == "/usr/bin/agent"
    assert preferred.calls == ["agent"]

    fallback = _which({"agent": None, "cursor-agent": "/usr/bin/cursor-agent"})
    report = diagnose(config, which=fallback)
    assert _status(report, "Agent").status == "OK"
    assert _status(report, "Agent").detail == "/usr/bin/cursor-agent"
    assert fallback.calls == ["agent", "cursor-agent"]


def test_cursor_missing_both_executables(tmp_path: Path) -> None:
    config = _benchmark(tmp_path, agent="cursor")
    report = diagnose(config, which=_which({"agent": None, "cursor-agent": None}))
    assert _status(report, "Agent").status == "FAIL"
    assert "agent and cursor-agent were not found" in _status(report, "Agent").detail


def test_authentication_is_always_skipped(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    report = diagnose(config, which=_which({"codex": "/usr/bin/codex"}))
    auth = _status(report, "Authentication")
    assert auth.status == "SKIP"
    assert "not checked" in auth.detail


def test_empty_docs_warns_without_failing(tmp_path: Path) -> None:
    config = _benchmark(tmp_path, docs_file=False)
    report = diagnose(config, which=_which({"codex": "/usr/bin/codex"}))
    assert _status(report, "Docs contents").status == "WARN"
    assert doctor_exit_code(report) == 0


def _runtime(root: Path, *, agent_image: str = "agent:latest", verifier_image: str = "verifier:latest") -> Path:
    path = root / "runtime.yaml"
    path.write_text(
        "version: 1\n"
        "backend: docker\n"
        "agents:\n"
        "  codex:\n"
        "    image: " + agent_image + "\n"
        "verifier:\n"
        "  image: " + verifier_image + "\n",
        encoding="utf-8",
    )
    return path


def _docker_runner(images: set[str], *, daemon: bool = True):
    calls: list[list[str]] = []

    def run(args):
        calls.append(list(args))
        if list(args) == ["docker", "info"]:
            return _completed(0 if daemon else 1)
        if list(args)[:3] == ["docker", "image", "inspect"]:
            return _completed(0 if args[3] in images else 1)
        raise AssertionError(args)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_docker_runtime_valid_does_not_require_host_provider(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    runtime = _runtime(tmp_path)
    which = _which({"docker": "/usr/local/bin/docker", "codex": None})
    runner_fn = _docker_runner({"agent:latest", "verifier:latest"})
    report = diagnose(config, runtime_config=runtime, which=which, docker_runner=runner_fn)
    assert _status(report, "Runtime config").status == "OK"
    assert _status(report, "Docker executable").detail == "/usr/local/bin/docker"
    assert _status(report, "Docker daemon").status == "OK"
    assert _status(report, "Agent image").detail == "agent:latest"
    assert _status(report, "Verifier image").detail == "verifier:latest"
    host = _status(report, "Provider host executable")
    assert host.status == "SKIP"
    assert "does not require" in host.detail
    assert "codex" not in which.calls
    assert all(call[1] in {"info", "image"} for call in runner_fn.calls)
    assert doctor_exit_code(report) == 0


def test_docker_executable_missing(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    report = diagnose(
        config,
        runtime_config=_runtime(tmp_path),
        which=_which({"docker": None}),
        docker_runner=_docker_runner(set()),
    )
    assert _status(report, "Docker executable").status == "FAIL"
    assert _status(report, "Docker daemon").status == "SKIP"
    assert _status(report, "Agent image").status == "SKIP"
    assert _status(report, "Provider host executable").status == "SKIP"
    assert doctor_exit_code(report) == 1


def test_docker_daemon_unavailable(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    report = diagnose(
        config,
        runtime_config=_runtime(tmp_path),
        which=_which({"docker": "/usr/bin/docker"}),
        docker_runner=_docker_runner({"agent:latest"}, daemon=False),
    )
    assert _status(report, "Docker daemon").status == "FAIL"
    assert _status(report, "Agent image").status == "SKIP"
    assert _status(report, "Verifier image").status == "SKIP"
    assert _status(report, "Docker daemon").detail == "Docker daemon is not available"


def test_docker_images_missing(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    runtime = _runtime(tmp_path)
    missing_agent = diagnose(
        config,
        runtime_config=runtime,
        which=_which({"docker": "/usr/bin/docker"}),
        docker_runner=_docker_runner({"verifier:latest"}),
    )
    assert _status(missing_agent, "Agent image").status == "FAIL"
    assert _status(missing_agent, "Verifier image").status == "OK"

    missing_verifier = diagnose(
        config,
        runtime_config=runtime,
        which=_which({"docker": "/usr/bin/docker"}),
        docker_runner=_docker_runner({"agent:latest"}),
    )
    assert _status(missing_verifier, "Verifier image").status == "FAIL"
    assert "not available locally" in _status(missing_verifier, "Verifier image").detail


def test_experiment_valid_invalid_and_missing_docs(tmp_path: Path) -> None:
    config = _benchmark(tmp_path)
    current = tmp_path / "docs-a"
    candidate = tmp_path / "docs-b"
    current.mkdir()
    candidate.mkdir()
    (current / "a.md").write_text("a\n", encoding="utf-8")
    (candidate / "b.md").write_text("b\n", encoding="utf-8")
    experiment = tmp_path / "docs-experiment.yaml"
    experiment.write_text(
        "version: 1\n"
        "reference: current\n"
        "variants:\n"
        "  - id: current\n"
        "    docs: ./docs-a\n"
        "  - id: candidate\n"
        "    docs: ./docs-b\n",
        encoding="utf-8",
    )
    report = diagnose(config, experiment_path=experiment, which=_which({"codex": "/bin/codex"}))
    assert _status(report, "Experiment").status == "OK"
    assert _status(report, "Reference").detail == "current"
    assert _status(report, "Variants").detail == "2"

    invalid = tmp_path / "bad.yaml"
    invalid.write_text("version: 1\nreference: current\nvariants: []\n", encoding="utf-8")
    bad = diagnose(config, experiment_path=invalid, which=_which({"codex": "/bin/codex"}))
    assert _status(bad, "Experiment").status == "FAIL"
    assert _status(bad, "Reference").status == "SKIP"

    missing = tmp_path / "missing-docs.yaml"
    missing.write_text(
        "version: 1\n"
        "reference: current\n"
        "variants:\n"
        "  - id: current\n"
        "    docs: ./nope-a\n"
        "  - id: candidate\n"
        "    docs: ./nope-b\n",
        encoding="utf-8",
    )
    gone = diagnose(config, experiment_path=missing, which=_which({"codex": "/bin/codex"}))
    assert _status(gone, "Experiment").status == "FAIL"
    assert "does not exist" in _status(gone, "Experiment").detail


def test_fail_exit_code_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _benchmark(tmp_path, agent="cursor")
    monkeypatch.setattr("agentdocs.doctor.shutil.which", lambda name: None)
    result = runner.invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 1
    assert "AgentDocsBench Doctor" in result.stdout
    assert "FAIL" in result.stdout
    assert "not checked" in result.stdout


def test_warn_cli_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _benchmark(tmp_path, docs_file=False)
    monkeypatch.setattr(
        "agentdocs.doctor.shutil.which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )
    result = runner.invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 0
    assert "WARN" in result.stdout


def test_doctor_does_not_launch_provider_task_or_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _benchmark(tmp_path)
    marker = tmp_path / "verifier-ran"
    (tmp_path / "verifiers" / "first_task" / "check.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def refuse(args, **kwargs):
        calls.append(list(args))
        raise AssertionError(args)

    monkeypatch.setattr("agentdocs.doctor.subprocess.run", refuse)
    report = diagnose(config, which=_which({"codex": "/usr/bin/codex"}))
    assert calls == []
    assert not marker.exists()
    assert doctor_exit_code(report) == 0
    assert _status(report, "Authentication").status == "SKIP"


def test_doctor_help_describes_a_read_only_check() -> None:
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "without" in result.stdout.lower() or "Does not run" in result.stdout
    assert "--runtime-config" in result.stdout
    assert "--experiment" in result.stdout


def test_init_help_mentions_agent_model_and_no_overwrite() -> None:
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "--agent" in result.stdout
    assert "--model" in result.stdout
    assert "not overwritten" in result.stdout.lower() or "already" in result.stdout.lower()


def test_root_help_lists_init_and_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    text = result.stdout
    assert "Run a benchmark." in text
    assert "Run a benchmark against multiple agent/model targets." in text
    assert "Run the same benchmark against multiple documentation variants." in text
    assert "Compare saved benchmark runs." in text
    assert "Compute the benchmark content fingerprint." in text
    assert "Show provider model discovery information." in text
    assert "Create a starter AgentDocsBench benchmark." in text
    assert "Check whether a benchmark environment is ready." in text
