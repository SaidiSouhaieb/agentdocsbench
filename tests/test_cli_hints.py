"""CLI hints stay additive. They do not change exit codes or echo secrets."""

from pathlib import Path

from typer.testing import CliRunner

from agentdocs.agents.base import AgentExecutableNotFoundError
from agentdocs.cli import app
from agentdocs.cli_hints import format_error_hint
from agentdocs.execution.errors import (
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
    RuntimeConfigError,
)
from agentdocs.experiment import ExperimentInvariantError
from agentdocs.github_actions import GitHubActionsError

runner = CliRunner()
_SECRET = "super-secret-token"


def test_known_errors_select_a_stable_hint() -> None:
    cases = [
        (FileNotFoundError("Config file not found: agentdocs.yaml"), "agentdocs init"),
        (FileNotFoundError("docs directory does not exist: ./docs"), "docs"),
        (FileNotFoundError("starter directory does not exist: ./starter"), "starter"),
        (FileNotFoundError("Verifier directory for task 'x' does not exist: ./v"), "verify.path"),
        (AgentExecutableNotFoundError("Cursor Agent executable not found."), "agentdocs doctor"),
        (DockerExecutableNotFoundError("docker missing"), "--runtime-config"),
        (DockerDaemonUnavailableError("daemon down"), "agentdocs doctor --runtime-config"),
        (DockerImageNotFoundError(f"Docker image 'mine:latest' {_SECRET}"), "does not pull"),
        (
            RuntimeConfigError("Runtime config has no agents.cursor entry."),
            "agents:",
        ),
        (
            GitHubActionsError("--github-actions requires a GitHub Actions job (GITHUB_ACTIONS=true)."),
            "--github-actions",
        ),
        (
            ExperimentInvariantError("Variant 'candidate' changed starter."),
            "documentation directory",
        ),
    ]
    for exc, needle in cases:
        hint = format_error_hint(exc)
        assert hint is not None
        assert needle in hint
        assert _SECRET not in hint


def test_unknown_exception_has_no_fabricated_hint() -> None:
    assert format_error_hint(RuntimeError(f"boom {_SECRET}")) is None
    assert format_error_hint(RuntimeConfigError("image must not be blank")) is None
    assert format_error_hint(GitHubActionsError("GITHUB_STEP_SUMMARY is not set.")) is None


def test_missing_config_keeps_exit_code_and_error_text(tmp_path: Path) -> None:
    missing = tmp_path / "agentdocs.yaml"
    result = runner.invoke(app, ["test", "--config", str(missing)])
    assert result.exit_code == 2
    assert f"Error: Config file not found: {missing}" in result.stderr
    assert "Hint:" in result.stderr
    assert "agentdocs init" in result.stderr
    assert _SECRET not in result.stderr


def test_missing_runtime_agent_entry_exits_with_a_hint(tmp_path: Path) -> None:
    config = tmp_path / "agentdocs.yaml"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "starter").mkdir()
    verifier = tmp_path / "verifiers" / "task"
    verifier.mkdir(parents=True)
    config.write_text(
        "version: 1\n"
        "docs: ./docs\n"
        "starter: ./starter\n"
        "agent:\n"
        "  type: cursor\n"
        "tasks:\n"
        "  - id: task\n"
        "    prompt: Do the task.\n"
        "    verify:\n"
        "      path: ./verifiers/task\n"
        "      command: python3 check.py\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime.yaml"
    runtime.write_text(
        "version: 1\nbackend: docker\nagents: {}\nverifier:\n  image: nope:latest\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "test",
            "--config",
            str(config),
            "--runtime-config",
            str(runtime),
            "--no-artifacts",
        ],
    )
    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "no agents.cursor entry" in result.stderr
    assert "Hint:" in result.stderr
    assert "agents:" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_hint_does_not_change_suite_exit_semantics() -> None:
    root = Path(__file__).resolve().parents[1] / "agentdocs"
    watched = [root / "suite.py", root / "experiment.py", root / "fingerprint.py", root / "workspace_changes.py"]
    watched.extend((root / "verifier").rglob("*.py"))
    watched.extend((root / "agents").rglob("*.py"))
    for path in watched:
        text = path.read_text(encoding="utf-8")
        assert "agentdocs.doctor" not in text
        assert "agentdocs.scaffold" not in text
        assert "cli_hints" not in text
