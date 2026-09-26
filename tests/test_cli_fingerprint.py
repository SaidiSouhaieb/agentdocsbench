from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs.cli import app
from agentdocs.fingerprint import compute_benchmark_fingerprint
from agentdocs.config import load_config

runner = CliRunner()

_CONFIG = """\
version: 1
docs: ./docs
starter: ./starter
agent:
  type: cursor
  model: model-a
tasks:
  - id: create_user
    prompt: Create Alice.
    verify:
      path: ./verifier
      command: python3 check.py
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "starter").mkdir()
    verifier = root / "verifier"
    verifier.mkdir()
    (root / "docs" / "users.md").write_bytes(b"users\n")
    (root / "starter" / "main.py").write_bytes(b"print(1)\n")
    (verifier / "check.py").write_text(
        "from pathlib import Path\nPath('ran.txt').write_text('ran')\n",
        encoding="utf-8",
    )
    path = root / "agentdocs.yaml"
    path.write_text(_CONFIG, encoding="utf-8")
    return path


def test_fingerprint_help_exits_zero() -> None:
    result = runner.invoke(app, ["fingerprint", "--help"])

    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "--json" in result.stdout


def test_fingerprint_prints_component_hashes_without_running_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)

    def fail_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fingerprint launched a process")

    monkeypatch.setattr("subprocess.run", fail_process)
    result = runner.invoke(app, ["fingerprint", "--config", str(config)])
    expected = compute_benchmark_fingerprint(load_config(config))

    assert result.exit_code == 0
    assert "AgentDocsBench Benchmark Fingerprint" in result.stdout
    assert "Algorithm: sha256" in result.stdout
    assert "Schema: 1" in result.stdout
    assert expected.docs_sha256 in result.stdout
    assert expected.starter_sha256 in result.stdout
    assert expected.tasks_sha256 in result.stdout
    assert expected.verifiers_sha256 in result.stdout
    assert expected.overall_sha256 in result.stdout
    assert not (config.parent / "verifier" / "ran.txt").exists()
    assert not (config.parent / ".agentdocs").exists()


def test_fingerprint_json_is_the_manifest(tmp_path: Path) -> None:
    config = _project(tmp_path)

    result = runner.invoke(app, ["fingerprint", "--config", str(config), "--json"])

    assert result.exit_code == 0
    assert '"schema_version": 1' in result.stdout
    assert '"overall_sha256"' in result.stdout
    assert "Create Alice." not in result.stdout


def test_missing_config_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["fingerprint", "--config", str(tmp_path / "missing.yaml")])

    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_missing_docs_directory_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path)
    shutil.rmtree(config.parent / "docs")

    result = runner.invoke(app, ["fingerprint", "--config", str(config)])

    assert result.exit_code == 2
    assert "does not exist" in result.stderr
