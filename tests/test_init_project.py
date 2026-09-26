"""Scaffold command coverage. These tests do not launch a provider or Docker."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs.cli import app
from agentdocs.config import load_config, validate_config_paths
from agentdocs.scaffold import InitProjectError, initialize_project

runner = CliRunner()


def _files(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return found


def test_creates_expected_structure(tmp_path: Path) -> None:
    target = initialize_project(tmp_path / "my-benchmark", agent_type="cursor")
    assert set(_files(target)) == {
        "README.md",
        "agentdocs.yaml",
        "docs/getting-started.md",
        "benchmark/starter/README.md",
        "benchmark/verifiers/first_task/check.py",
    }
    assert "HELLO_AGENTDOCS" in (target / "docs" / "getting-started.md").read_text(encoding="utf-8")


def test_generated_config_loads_and_paths_validate(tmp_path: Path) -> None:
    target = initialize_project(tmp_path / "bench", agent_type="codex")
    loaded = load_config(target / "agentdocs.yaml")
    validate_config_paths(loaded)
    assert loaded.tasks[0].id == "first_task"
    assert loaded.tasks[0].verify.command == "python3 check.py"


def test_requested_agent_and_model_are_stored(tmp_path: Path) -> None:
    target = initialize_project(tmp_path / "bench", agent_type="claude", model="claude-sonnet")
    loaded = load_config(target / "agentdocs.yaml")
    assert loaded.agent.type == "claude"
    assert loaded.agent.model == "claude-sonnet"


def test_omitted_model_stays_absent(tmp_path: Path) -> None:
    target = initialize_project(tmp_path / "bench", agent_type="cursor")
    text = (target / "agentdocs.yaml").read_text(encoding="utf-8")
    loaded = load_config(target / "agentdocs.yaml")
    assert loaded.agent.model is None
    assert "model:" not in text


def test_unsupported_agent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InitProjectError, match="Unsupported agent type"):
        initialize_project(tmp_path / "bench", agent_type="nope")
    assert not (tmp_path / "bench").exists()


def test_blank_model_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InitProjectError, match="Model must not be empty"):
        initialize_project(tmp_path / "bench", agent_type="codex", model="  ")
    assert not (tmp_path / "bench").exists()


def test_new_directory_and_existing_empty_directory(tmp_path: Path) -> None:
    created = initialize_project(tmp_path / "fresh", agent_type="cursor")
    assert created.is_dir()
    empty = tmp_path / "empty"
    empty.mkdir()
    filled = initialize_project(empty, agent_type="codex")
    assert (filled / "agentdocs.yaml").is_file()


def test_non_empty_directory_is_refused_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "busy"
    target.mkdir()
    keep = target / "keep.txt"
    keep.write_text("mine\n", encoding="utf-8")
    with pytest.raises(InitProjectError, match="Target directory is not empty"):
        initialize_project(target, agent_type="cursor")
    assert keep.read_text(encoding="utf-8") == "mine\n"
    assert not (target / "agentdocs.yaml").exists()
    message = ""
    try:
        initialize_project(target, agent_type="cursor")
    except InitProjectError as exc:
        message = str(exc)
    assert "Choose an empty directory or a new directory." in message


def test_relative_path_creates_under_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    created = initialize_project(Path("my-benchmark"), agent_type="cursor")
    assert created == Path("my-benchmark")
    assert (tmp_path / "my-benchmark" / "agentdocs.yaml").is_file()


def test_scaffold_contents_are_deterministic(tmp_path: Path) -> None:
    first = initialize_project(tmp_path / "one", agent_type="cursor", model="composer-2")
    second = initialize_project(tmp_path / "two", agent_type="cursor", model="composer-2")
    assert _files(first) == _files(second)


def test_generated_verifier_pass_and_fail(tmp_path: Path) -> None:
    target = initialize_project(tmp_path / "bench", agent_type="cursor")
    project = target / "benchmark" / "starter"
    verifier = target / "benchmark" / "verifiers" / "first_task" / "check.py"
    env = os.environ.copy()
    env["AGENTDOCS_PROJECT_DIR"] = str(project.resolve())

    missing = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert missing.returncode != 0
    assert "missing result.txt" in missing.stderr

    (project / "result.txt").write_text("nope\n", encoding="utf-8")
    wrong = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert wrong.returncode != 0
    assert "HELLO_AGENTDOCS" in wrong.stderr

    (project / "result.txt").write_text("HELLO_AGENTDOCS\n", encoding="utf-8")
    ok = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert ok.returncode == 0


def test_failed_write_does_not_leave_a_half_created_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.write_text

    def fail_check(self: Path, data: str, *args, **kwargs):
        if self.name == "check.py":
            raise OSError("disk full")
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_check)
    fresh = tmp_path / "fresh"
    with pytest.raises(OSError, match="disk full"):
        initialize_project(fresh, agent_type="cursor")
    assert not fresh.exists()
    assert not any(path.name.startswith(".fresh.") for path in tmp_path.iterdir())

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(OSError, match="disk full"):
        initialize_project(empty, agent_type="cursor")
    assert list(empty.rglob("*")) == []


def test_cli_prints_next_steps(tmp_path: Path) -> None:
    target = tmp_path / "my-benchmark"
    result = runner.invoke(app, ["init", str(target), "--agent", "cursor"])
    assert result.exit_code == 0
    assert "Created AgentDocsBench benchmark:" in result.stdout
    assert "agentdocs doctor --config agentdocs.yaml" in result.stdout
    assert "agentdocs test --config agentdocs.yaml" in result.stdout
    assert "currently uses cursor" in result.stdout
    assert (target / "agentdocs.yaml").is_file()


def test_cli_refuses_non_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "busy"
    target.mkdir()
    (target / "keep.txt").write_text("mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(target), "--agent", "cursor"])
    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "Target directory is not empty" in result.stderr
    assert "Choose an empty directory or a new directory." in result.stderr
    assert (target / "keep.txt").read_text(encoding="utf-8") == "mine\n"
