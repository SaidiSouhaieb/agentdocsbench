from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs.config import ConfigError, load_config
from agentdocs.config.with_docs import with_docs
from agentdocs.experiment_config import load_experiment_config
from agentdocs.fingerprint import compute_benchmark_fingerprint
from tests.experiment_support import PROMPT, load_pair, write_benchmark


def _write(directory: Path, contents: str, name: str = "docs-experiment.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(contents, encoding="utf-8")
    return path


def _valid(directory: Path) -> str:
    reference = directory / "reference"
    candidate = directory / "candidate"
    reference.mkdir()
    candidate.mkdir()
    (reference / "a.md").write_text("one\n", encoding="utf-8")
    (candidate / "a.md").write_text("two\n", encoding="utf-8")
    return f"""\
version: 1
reference: "  current  "
variants:
  - id: "  current  "
    docs: {reference}
  - id: candidate
    docs: ./candidate
"""


def test_valid_two_variant_config_trims_ids(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid(tmp_path))

    loaded = load_experiment_config(path)

    assert loaded.version == 1
    assert loaded.reference == "current"
    assert [item.id for item in loaded.variants] == ["current", "candidate"]
    assert loaded.variants[0].docs == (tmp_path / "reference").resolve()
    assert loaded.variants[1].docs == (tmp_path / "candidate").resolve()


def test_more_than_two_variants(tmp_path: Path) -> None:
    for name in ("one", "two", "three"):
        (tmp_path / name).mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: two
variants:
  - id: one
    docs: ./one
  - id: two
    docs: ./two
  - id: three
    docs: ./three
""",
    )

    loaded = load_experiment_config(path)

    assert [item.id for item in loaded.variants] == ["one", "two", "three"]
    assert loaded.reference == "two"


def test_relative_docs_resolve_against_the_experiment_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    decoy = tmp_path / "docs" / "reference"
    decoy.mkdir(parents=True)
    (project / "docs" / "reference").mkdir(parents=True)
    (project / "docs" / "candidate").mkdir(parents=True)
    path = _write(
        project,
        """\
version: 1
reference: reference
variants:
  - id: reference
    docs: ./docs/reference
  - id: candidate
    docs: ./docs/candidate
""",
    )
    monkeypatch.chdir(tmp_path)

    loaded = load_experiment_config(path)

    assert loaded.variants[0].docs == (project / "docs" / "reference").resolve()
    assert loaded.variants[0].docs != decoy.resolve()


def test_absolute_docs_path(tmp_path: Path) -> None:
    loaded = load_experiment_config(_write(tmp_path, _valid(tmp_path)))

    assert loaded.variants[0].docs.is_absolute()


def test_blank_id_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: "   "
    docs: ./docs
  - id: current
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Variant id must not be empty"):
        load_experiment_config(path)


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: current
    docs: ./docs
  - id: " current "
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Duplicate variant id"):
        load_experiment_config(path)


def test_blank_reference_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: "  "
variants:
  - id: current
    docs: ./docs
  - id: candidate
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Reference variant must not be empty"):
        load_experiment_config(path)


def test_missing_reference_variant_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: missing
variants:
  - id: current
    docs: ./docs
  - id: candidate
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="must match exactly one variant id"):
        load_experiment_config(path)


@pytest.mark.parametrize(
    "contents",
    [
        "version: 1\nreference: only\nvariants:\n  - id: only\n    docs: ./docs\n",
        "version: 1\nreference: only\nvariants: []\n",
    ],
)
def test_fewer_than_two_variants_is_rejected(tmp_path: Path, contents: str) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(tmp_path, contents)

    with pytest.raises(ConfigError, match="At least two documentation variants"):
        load_experiment_config(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
agent: cursor
variants:
  - id: current
    docs: ./docs
  - id: candidate
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Extra inputs"):
        load_experiment_config(path)


def test_unknown_variant_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: current
    docs: ./docs
    model: named
  - id: candidate
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Extra inputs"):
        load_experiment_config(path)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 2
reference: current
variants:
  - id: current
    docs: ./docs
  - id: candidate
    docs: ./docs
""",
    )

    with pytest.raises(ConfigError, match="Unsupported experiment version"):
        load_experiment_config(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Experiment file not found"):
        load_experiment_config(tmp_path / "missing.yaml")


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: [\n")

    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_experiment_config(path)


def test_directory_instead_of_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not a file"):
        load_experiment_config(tmp_path)


def test_missing_docs_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "present").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: current
    docs: ./present
  - id: candidate
    docs: ./missing
""",
    )

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_experiment_config(path)


def test_docs_path_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "present").mkdir()
    (tmp_path / "file.txt").write_text("x\n", encoding="utf-8")
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: current
    docs: ./present
  - id: candidate
    docs: ./file.txt
""",
    )

    with pytest.raises(NotADirectoryError, match="not a directory"):
        load_experiment_config(path)


def test_same_docs_directory_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    path = _write(
        tmp_path,
        """\
version: 1
reference: current
variants:
  - id: current
    docs: ./docs
  - id: control
    docs: ./docs
""",
    )

    loaded = load_experiment_config(path)

    assert loaded.variants[0].docs == loaded.variants[1].docs


def test_with_docs_replaces_only_the_docs_path(tmp_path: Path) -> None:
    config, _experiment = load_pair(tmp_path)
    original_docs = config.docs
    original_tasks = config.tasks
    replacement = tmp_path / "docs" / "candidate"

    copied = with_docs(config, replacement)

    assert config.docs == original_docs
    assert copied.docs == replacement
    assert copied.starter == config.starter
    assert copied.agent == config.agent
    assert copied.agent.model == config.agent.model
    assert copied.tasks is original_tasks
    assert copied.tasks[0].prompt == PROMPT
    assert copied.tasks[0].verify.command == config.tasks[0].verify.command


def test_docs_bytes_change_only_docs_and_overall_fingerprints(tmp_path: Path) -> None:
    config, experiment = load_pair(tmp_path)
    reference = compute_benchmark_fingerprint(with_docs(config, experiment.variants[0].docs))
    candidate = compute_benchmark_fingerprint(with_docs(config, experiment.variants[1].docs))

    assert reference.docs_sha256 != candidate.docs_sha256
    assert reference.overall_sha256 != candidate.overall_sha256
    assert reference.starter_sha256 == candidate.starter_sha256
    assert reference.tasks_sha256 == candidate.tasks_sha256
    assert reference.verifiers_sha256 == candidate.verifiers_sha256


def test_identical_docs_keep_every_fingerprint_component(tmp_path: Path) -> None:
    config, _experiment = load_pair(tmp_path)
    first = compute_benchmark_fingerprint(config)
    second = compute_benchmark_fingerprint(with_docs(config, config.docs))

    assert first.docs_sha256 == second.docs_sha256
    assert first.overall_sha256 == second.overall_sha256
    assert first.starter_sha256 == second.starter_sha256
    assert first.tasks_sha256 == second.tasks_sha256
    assert first.verifiers_sha256 == second.verifiers_sha256


def test_base_docs_directory_is_not_required_for_loading(tmp_path: Path) -> None:
    config_path, _experiment_path = write_benchmark(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("./docs/reference", "./docs/does-not-exist"),
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert not loaded.docs.exists()
