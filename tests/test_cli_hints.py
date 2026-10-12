"""CLI hints stay additive. They do not change exit codes or echo secrets."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs.agents.base import AgentExecutableNotFoundError, AgentTimeoutError
from agentdocs.cli import app
from agentdocs.cli_hints import format_error_hint
from agentdocs.config import ConfigError
from agentdocs.execution.errors import (
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
    RuntimeConfigError,
)
from agentdocs.experiment import ExperimentInvariantError
from agentdocs.github_actions import GitHubActionsError
from agentdocs.models import ModelDiscoveryError, ModelDiscoveryResult
from agentdocs.verifier.errors import (
    VerifierCommandError,
    VerifierExecutableNotFoundError,
    VerifierTimeoutError,
)
from agentdocs.workspace import WorkspaceError

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
        (ConfigError(f"Invalid YAML in {_SECRET}: bad syntax"), "YAML syntax"),
        (RuntimeConfigError(f"Invalid YAML in {_SECRET}: bad syntax"), "YAML syntax"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\nversion: Unsupported version 2."), "`version`"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\nagent.type: Unsupported agent type 'other'."), "`agent.type`"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\nagent.model: Model must not be empty."), "`agent.model`"),
        (ValueError("Model must not be empty."), "`--model`"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\ntasks.0.id: Task id must not be empty."), "`id`"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\ntasks.0.prompt: Task prompt must not be empty."), "`prompt`"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\nDuplicate task id(s): '{_SECRET}'."), "unique"),
        (ConfigError(f"Invalid configuration in {_SECRET}:\ntasks.0.verify.command: Verifier command must not be empty."), "`verify.command`"),
        (WorkspaceError(f"Workspace source contains symbolic link:\n{_SECRET}"), "symbolic links"),
        (VerifierCommandError(f"Verifier command could not be parsed: {_SECRET}"), "shell quoting"),
        (VerifierExecutableNotFoundError(f"Verifier executable '{_SECRET}' was not found."), "on PATH"),
        (VerifierTimeoutError(f"Verifier did not finish: {_SECRET}"), "verifier command"),
        (AgentTimeoutError(f"Agent timed out: {_SECRET}"), "provider CLI"),
        (ModelDiscoveryError(f"Model catalog failed: {_SECRET}"), "agentdocs models"),
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
    assert format_error_hint(ConfigError("Invalid configuration in agentdocs.yaml:\ntasks: Missing field")) is None
    assert format_error_hint(ConfigError(f"Invalid configuration in {_SECRET}/Unsupported version 2:\ntasks: Missing field")) is None
    assert format_error_hint(ConfigError("Matrix file is empty: matrix.yaml")) is None
    assert format_error_hint(ValueError(f"Model unavailable: {_SECRET}")) is None
    assert format_error_hint(WorkspaceError(f"Copy failed: {_SECRET}")) is None


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


_VALID_CONFIG = (
    "version: 1\n"
    "docs: ./docs\n"
    "starter: ./starter\n"
    "agent:\n"
    "  type: codex\n"
    "tasks:\n"
    "  - id: task\n"
    "    prompt: Do the task.\n"
    "    verify:\n"
    "      path: ./verifier\n"
    "      command: python3 check.py\n"
)


def _project(tmp_path: Path, contents: str = _VALID_CONFIG) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("Guide\n", encoding="utf-8")
    (tmp_path / "starter").mkdir()
    (tmp_path / "verifier").mkdir()
    (tmp_path / "verifier" / "check.py").write_text("pass\n", encoding="utf-8")
    config = tmp_path / "agentdocs.yaml"
    config.write_text(contents, encoding="utf-8")
    return config


@pytest.mark.parametrize(
    ("contents", "error", "hint"),
    [
        ("version: [\n", "Invalid YAML", "YAML syntax"),
        (_VALID_CONFIG.replace("version: 1", "version: 2"), "Unsupported version 2", "`version`"),
        (_VALID_CONFIG.replace("type: codex", "type: unknown"), "Unsupported agent type 'unknown'", "`agent.type`"),
        (_VALID_CONFIG.replace("type: codex", "type: codex\n  model: '  '"), "Model must not be empty", "`agent.model`"),
        (_VALID_CONFIG.replace("id: task", "id: '  '"), "Task id must not be empty", "non-empty `id`"),
        (_VALID_CONFIG.replace("prompt: Do the task.", "prompt: '  '"), "Task prompt must not be empty", "`prompt`"),
        (_VALID_CONFIG + "  - id: task\n    prompt: Another task.\n    verify:\n      path: ./verifier\n      command: python3 check.py\n", "Duplicate task id", "unique"),
        (_VALID_CONFIG.replace("command: python3 check.py", "command: '  '"), "Verifier command must not be empty", "`verify.command`"),
    ],
)
def test_config_failure_shows_hint_without_changing_error(
    tmp_path: Path, contents: str, error: str, hint: str
) -> None:
    config = _project(tmp_path, contents)
    result = runner.invoke(app, ["test", "--config", str(config), "--no-artifacts"])
    assert result.exit_code == 2
    assert f"Error: " in result.stderr
    assert error in result.stderr
    assert "Hint:" in result.stderr
    assert hint in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("command", "option", "filename"),
    [
        ("test", "--runtime-config", "runtime.yaml"),
        ("matrix", "--matrix", "matrix.yaml"),
        ("experiment", "--experiment", "docs-experiment.yaml"),
    ],
)
def test_malformed_command_yaml_shows_shared_hint(
    tmp_path: Path, command: str, option: str, filename: str
) -> None:
    config = _project(tmp_path)
    malformed = tmp_path / filename
    malformed.write_text("version: [\n", encoding="utf-8")

    result = runner.invoke(
        app, [command, "--config", str(config), option, str(malformed)]
    )

    assert result.exit_code == 2
    assert f"Error: Invalid YAML in {malformed}:" in result.stderr
    assert "Hint: Check the YAML syntax and indentation in the selected input file." in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_matrix_validation_error_has_no_benchmark_specific_hint(tmp_path: Path) -> None:
    config = _project(tmp_path)
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text("version: 2\ntargets: []\n", encoding="utf-8")

    result = runner.invoke(
        app, ["matrix", "--config", str(config), "--matrix", str(matrix)]
    )

    assert result.exit_code == 2
    assert f"Error: Invalid matrix configuration in {matrix}:" in result.stderr
    assert "Unsupported matrix version 2" in result.stderr
    assert "Hint:" not in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_blank_model_override_shows_hint(tmp_path: Path) -> None:
    config = _project(tmp_path)
    result = runner.invoke(app, ["test", "--config", str(config), "--model", "  ", "--no-artifacts"])
    assert result.exit_code == 2
    assert "Error: Model must not be empty." in result.stderr
    assert "Hint: Pass a non-empty model ID to `--model`." in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (WorkspaceError("Workspace source contains symbolic link:\nsecret-link"), "symbolic links"),
        (VerifierCommandError("Verifier command could not be parsed: bad quote"), "shell quoting"),
        (VerifierExecutableNotFoundError("Verifier executable 'missing' was not found."), "on PATH"),
        (VerifierTimeoutError("Verifier did not finish within 60 seconds."), "verifier command"),
        (AgentTimeoutError("Agent did not finish within 60 seconds."), "provider CLI"),
    ],
)
def test_runtime_failure_shows_hint_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, hint: str
) -> None:
    config = _project(tmp_path)

    def fail_suite(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("agentdocs.cli.run_suite", fail_suite)
    result = runner.invoke(app, ["test", "--config", str(config), "--no-artifacts"])
    assert result.exit_code == 2
    assert f"Error: {error}" in result.stderr
    assert f"Hint: " in result.stderr
    assert hint in result.stderr
    if isinstance(error, (VerifierTimeoutError, AgentTimeoutError)):
        assert "--timeout" not in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_model_discovery_failure_and_missing_cli_show_next_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_all() -> tuple[ModelDiscoveryResult, ...]:
        return (
            ModelDiscoveryResult(agent="codex", models=(), status="failed", message=f"Model listing exited 1. {_SECRET}"),
            ModelDiscoveryResult(agent="claude", models=(), status="unsupported", message="Use /model."),
            ModelDiscoveryResult(agent="cursor", models=(), status="executable_not_found", message="CLI executable was not found."),
        )

    monkeypatch.setattr("agentdocs.cli.discover_all_models", fake_all)
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "Codex\n" in result.stdout
    assert "Discovery: failed" in result.stdout
    assert "Model listing exited 1." in result.stdout
    assert "Hint: Check the provider CLI installation and authentication" in result.stdout
    assert "Discovery: unsupported by installed CLI" in result.stdout
    assert "Use /model." in result.stdout
    assert "CLI: not installed" in result.stdout
    assert "Hint: Install the provider CLI" in result.stdout
    assert result.stdout.count("Hint:") == 2
    assert all(_SECRET not in line for line in result.stdout.splitlines() if "Hint:" in line)
    assert "Traceback" not in result.stdout + result.stderr


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
