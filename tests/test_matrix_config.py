from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs.config import ConfigError
from agentdocs.matrix_config import load_matrix_config

_VALID = """\
version: 1
targets:
  - id: cursor-default
    agent: cursor
  - id: "  cursor-named  "
    agent: " cursor "
    model: "  model-a  "
  - id: same-again
    agent: cursor
    model: model-a
"""


def _write(directory: Path, contents: str, name: str = "matrix.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_valid_matrix_keeps_order_and_trims(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID)

    matrix = load_matrix_config(path)

    assert matrix.version == 1
    assert [target.id for target in matrix.targets] == [
        "cursor-default",
        "cursor-named",
        "same-again",
    ]
    assert matrix.targets[0].agent == "cursor"
    assert matrix.targets[0].model is None
    assert matrix.targets[1].agent == "cursor"
    assert matrix.targets[1].model == "model-a"
    assert matrix.targets[2].model == "model-a"


def test_one_target_is_valid(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "version: 1\ntargets:\n  - id: only\n    agent: codex\n",
    )

    matrix = load_matrix_config(path)

    assert len(matrix.targets) == 1
    assert matrix.targets[0].model is None


def test_blank_model_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "version: 1\ntargets:\n  - id: named\n    agent: cursor\n    model: '   '\n",
    )

    with pytest.raises(ConfigError, match="Model must not be empty"):
        load_matrix_config(path)


def test_blank_id_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "version: 1\ntargets:\n  - id: '   '\n    agent: cursor\n",
    )

    with pytest.raises(ConfigError, match="Target id must not be empty"):
        load_matrix_config(path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
targets:
  - id: same
    agent: cursor
  - id: same
    agent: codex
""",
    )

    with pytest.raises(ConfigError, match="Duplicate target id"):
        load_matrix_config(path)


def test_unsupported_agent_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "version: 1\ntargets:\n  - id: other\n    agent: gemini\n",
    )

    with pytest.raises(ConfigError, match="Unsupported agent type 'gemini'"):
        load_matrix_config(path)


def test_empty_targets_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 1\ntargets: []\n")

    with pytest.raises(ConfigError, match="At least one matrix target"):
        load_matrix_config(path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "version: 1\nnote: extra\ntargets:\n  - id: only\n    agent: codex\n",
    )

    with pytest.raises(ConfigError, match="Extra inputs"):
        load_matrix_config(path)


def test_malformed_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: [\n")

    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_matrix_config(path)


def test_missing_matrix_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(FileNotFoundError, match="Matrix file not found"):
        load_matrix_config(missing)


def test_matrix_path_that_is_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Matrix path is not a file"):
        load_matrix_config(tmp_path)
