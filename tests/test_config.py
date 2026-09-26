from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs.config import ConfigError, load_config, validate_config_paths

VALID_CONFIG = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: create_user
    prompt: |
      Using the provided documentation,
      modify the project so it creates
      a user named Alice.
    verify:
      path: ./benchmark/verifiers/create_user
      command: pytest tests/test_user.py
  - id: enable_oauth
    prompt: |
      Using the provided documentation,
      configure Google OAuth.
    verify:
      path: ./benchmark/verifiers/enable_oauth
      command: pytest tests/test_oauth.py
"""


def _write_config(directory: Path, contents: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "agentdocs.yaml"
    path.write_text(contents, encoding="utf-8")
    return path


def _quoted(path: Path) -> str:
    escaped = path.as_posix().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def test_valid_config_loads(tmp_path: Path) -> None:
    project = tmp_path / "project"
    docs = project / "docs"
    starter = project / "benchmark" / "starter"
    docs.mkdir(parents=True)
    starter.mkdir(parents=True)
    (project / "benchmark" / "verifiers" / "create_user").mkdir(parents=True)
    (project / "benchmark" / "verifiers" / "enable_oauth").mkdir(parents=True)
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    assert config.version == 1
    assert config.agent.type == "codex"
    assert config.docs == docs.resolve()
    assert config.starter == starter.resolve()
    assert [task.id for task in config.tasks] == ["create_user", "enable_oauth"]
    assert "Alice" in config.tasks[0].prompt
    assert config.tasks[0].verify.command == "pytest tests/test_user.py"
    assert config.tasks[0].verify.path == (
        project / "benchmark" / "verifiers" / "create_user"
    ).resolve()
    assert config.tasks[1].verify.command == "pytest tests/test_oauth.py"
    assert config.tasks[1].verify.path == (
        project / "benchmark" / "verifiers" / "enable_oauth"
    ).resolve()
    validate_config_paths(config)


def test_relative_paths_resolve_against_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    config_path = _write_config(project, VALID_CONFIG)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_config(config_path)

    assert config.docs == (project / "docs").resolve()
    assert config.starter == (project / "benchmark" / "starter").resolve()
    assert config.tasks[0].verify.path == (
        project / "benchmark" / "verifiers" / "create_user"
    ).resolve()
    assert config.docs != (elsewhere / "docs").resolve()
    assert config.tasks[0].verify.path != (
        elsewhere / "benchmark" / "verifiers" / "create_user"
    ).resolve()


def test_absolute_paths_stay_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "absolute-docs"
    starter = tmp_path / "absolute-starter"
    verifier = tmp_path / "absolute-verifier"
    docs.mkdir()
    starter.mkdir()
    verifier.mkdir()
    project = tmp_path / "project"
    contents = (
        VALID_CONFIG.replace("docs: ./docs", f"docs: {_quoted(docs)}")
        .replace("starter: ./benchmark/starter", f"starter: {_quoted(starter)}")
        .replace(
            "path: ./benchmark/verifiers/create_user",
            f"path: {_quoted(verifier)}",
        )
    )
    config_path = _write_config(project, contents)
    monkeypatch.chdir(tmp_path)

    config = load_config(str(config_path))

    assert config.docs == docs.resolve()
    assert config.starter == starter.resolve()
    assert config.tasks[0].verify.path == verifier.resolve()
    assert config.docs.is_absolute()
    assert config.starter.is_absolute()
    assert config.tasks[0].verify.path.is_absolute()


def test_missing_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(missing)


def test_invalid_yaml(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "project", "version: [\n")

    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(config_path)


def test_unsupported_version(tmp_path: Path) -> None:
    contents = VALID_CONFIG.replace("version: 1", "version: 2", 1)
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Unsupported version 2"):
        load_config(config_path)


def test_supported_agent_types(tmp_path: Path) -> None:
    for agent_type in ("codex", "claude", "cursor"):
        contents = VALID_CONFIG.replace("type: codex", f"type: {agent_type}", 1)
        config_path = _write_config(tmp_path / agent_type, contents)
        assert load_config(config_path).agent.type == agent_type


def test_omitted_model_is_none(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "project", VALID_CONFIG)

    assert load_config(config_path).agent.model is None


def test_model_is_loaded_and_trimmed(tmp_path: Path) -> None:
    contents = VALID_CONFIG.replace("type: codex", "type: cursor\n  model: '  model-a  '", 1)
    config_path = _write_config(tmp_path / "project", contents)

    config = load_config(config_path)

    assert config.agent.type == "cursor"
    assert config.agent.model == "model-a"


def test_blank_model_is_rejected(tmp_path: Path) -> None:
    contents = VALID_CONFIG.replace("type: codex", "type: cursor\n  model: '   '", 1)
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Model must not be empty"):
        load_config(config_path)


def test_unsupported_agent(tmp_path: Path) -> None:
    contents = VALID_CONFIG.replace("type: codex", "type: gemini", 1)
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Unsupported agent type 'gemini'"):
        load_config(config_path)


def test_empty_tasks(tmp_path: Path) -> None:
    contents = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks: []
"""
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="At least one task is required"):
        load_config(config_path)


def test_duplicate_task_ids(tmp_path: Path) -> None:
    contents = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: create_user
    prompt: Create a user.
    verify:
      path: ./benchmark/verifiers/create_user
      command: pytest tests/test_user.py
  - id: create_user
    prompt: Create the user again.
    verify:
      path: ./benchmark/verifiers/create_user
      command: pytest tests/test_user_again.py
"""
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Duplicate task id"):
        load_config(config_path)


def test_empty_task_id(tmp_path: Path) -> None:
    contents = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: ""
    prompt: Create a user.
    verify:
      path: ./benchmark/verifiers/create_user
      command: pytest tests/test_user.py
"""
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Task id must not be empty"):
        load_config(config_path)


def test_empty_prompt(tmp_path: Path) -> None:
    contents = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: create_user
    prompt: ""
    verify:
      path: ./benchmark/verifiers/create_user
      command: pytest tests/test_user.py
"""
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Task prompt must not be empty"):
        load_config(config_path)


def test_empty_verifier_command(tmp_path: Path) -> None:
    contents = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: create_user
    prompt: Create a user.
    verify:
      path: ./benchmark/verifiers/create_user
      command: ""
"""
    config_path = _write_config(tmp_path / "project", contents)

    with pytest.raises(ConfigError, match="Verifier command must not be empty"):
        load_config(config_path)


def test_missing_docs_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "benchmark" / "starter").mkdir(parents=True)
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    with pytest.raises(FileNotFoundError, match="docs directory does not exist"):
        validate_config_paths(config)


def test_missing_starter_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    with pytest.raises(FileNotFoundError, match="starter directory does not exist"):
        validate_config_paths(config)


def test_docs_path_must_be_a_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "benchmark" / "starter").mkdir(parents=True)
    config_path = _write_config(project, VALID_CONFIG)
    (project / "docs").write_text("not a directory", encoding="utf-8")

    config = load_config(config_path)

    with pytest.raises(NotADirectoryError, match="docs path is not a directory"):
        validate_config_paths(config)


def test_starter_path_must_be_a_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    starter = project / "benchmark" / "starter"
    starter.parent.mkdir(parents=True)
    starter.write_text("not a directory", encoding="utf-8")
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    with pytest.raises(NotADirectoryError, match="starter path is not a directory"):
        validate_config_paths(config)


def test_missing_verifier_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "benchmark" / "starter").mkdir(parents=True)
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    with pytest.raises(
        FileNotFoundError,
        match="Verifier directory for task 'create_user' does not exist",
    ):
        validate_config_paths(config)


def test_verifier_path_must_be_a_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "benchmark" / "starter").mkdir(parents=True)
    verifier = project / "benchmark" / "verifiers" / "create_user"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("not a directory", encoding="utf-8")
    (project / "benchmark" / "verifiers" / "enable_oauth").mkdir()
    config_path = _write_config(project, VALID_CONFIG)

    config = load_config(config_path)

    with pytest.raises(
        NotADirectoryError,
        match="Verifier path for task 'create_user' is not a directory",
    ):
        validate_config_paths(config)
