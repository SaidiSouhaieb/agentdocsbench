"""Runtime config schema version 1."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentdocs.execution.errors import RuntimeConfigError
from agentdocs.runtime_config import load_runtime_config, require_env_passthrough


def _write(directory: Path, text: str, name: str = "runtime.yaml") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _valid(image: str = "agentdocs-cursor:local") -> str:
    return f"""\
version: 1
backend: docker
agents:
  cursor:
    image: {image}
    network: bridge
verifier:
  image: agentdocs-verifier:local
"""


def test_valid_runtime_config_loads(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid())

    loaded = load_runtime_config(path)

    assert loaded.version == 1
    assert loaded.backend == "docker"
    cursor = loaded.agents.cursor
    assert cursor is not None
    assert cursor.image == "agentdocs-cursor:local"
    assert cursor.network == "bridge"
    assert cursor.env_passthrough == []
    assert cursor.mounts == []
    assert loaded.agents.codex is None
    assert loaded.verifier.image == "agentdocs-verifier:local"


def test_read_only_defaults_to_true(tmp_path: Path) -> None:
    source = tmp_path / "token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {source}
        target: /opt/cursor/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    loaded = load_runtime_config(path)

    cursor = loaded.agents.cursor
    assert cursor is not None
    assert cursor.mounts[0].read_only is True


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_runtime_config(tmp_path / "missing.yaml")


def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigError, match="not a file"):
        load_runtime_config(tmp_path)


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: [\n")

    with pytest.raises(RuntimeConfigError, match="Invalid YAML"):
        load_runtime_config(path)


def test_wrong_version_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid().replace("version: 1", "version: 2", 1))

    with pytest.raises(RuntimeConfigError, match="version"):
        load_runtime_config(path)


def test_backend_other_than_docker_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid().replace("backend: docker", "backend: podman", 1))

    with pytest.raises(RuntimeConfigError, match="backend"):
        load_runtime_config(path)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid() + "pull: true\n")

    with pytest.raises(RuntimeConfigError, match="pull"):
        load_runtime_config(path)


def test_plaintext_env_mapping_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    env:
      API_KEY: secret
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="env"):
        load_runtime_config(path)


def test_blank_image_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid(image='"   "'))

    with pytest.raises(RuntimeConfigError, match="image"):
        load_runtime_config(path)


def test_unsupported_network_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid().replace("network: bridge", "network: host", 1))

    with pytest.raises(RuntimeConfigError, match="network"):
        load_runtime_config(path)


def test_missing_provider_entry_is_reported_for_that_agent(tmp_path: Path) -> None:
    loaded = load_runtime_config(_write(tmp_path, _valid()))

    with pytest.raises(RuntimeConfigError, match="agents.codex"):
        loaded.agents.for_agent("codex")


def test_relative_mount_source_resolves_against_the_config_directory(tmp_path: Path) -> None:
    source = tmp_path / "token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: token
        target: /opt/cursor/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    loaded = load_runtime_config(path)

    cursor = loaded.agents.cursor
    assert cursor is not None
    assert Path(cursor.mounts[0].source) == source.resolve()


def test_tilde_mount_source_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "cursor-token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: ~/cursor-token
        target: /opt/cursor/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    loaded = load_runtime_config(path)

    cursor = loaded.agents.cursor
    assert cursor is not None
    assert Path(cursor.mounts[0].source) == source.resolve()


def test_missing_mount_source_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: missing-token
        target: /opt/cursor/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="does not exist"):
        load_runtime_config(path)


def test_relative_container_target_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {source}
        target: opt/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="absolute"):
        load_runtime_config(path)


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/workspace",
        "/workspace/project",
        "/workspace/docs",
        "/workspace/verifier",
        "/workspace/project/nested",
    ],
)
def test_reserved_mount_target_is_rejected(tmp_path: Path, target: str) -> None:
    source = tmp_path / "token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {source}
        target: {target}
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="reserved"):
        load_runtime_config(path)


def test_docker_socket_target_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "token"
    source.write_text("x", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {source}
        target: /var/run/docker.sock
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="Docker socket"):
        load_runtime_config(path)


def test_docker_socket_source_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: /var/run/docker.sock
        target: /opt/cursor/sock
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="Docker socket"):
        load_runtime_config(path)


def test_root_source_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: /
        target: /opt/host-root
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="filesystem root"):
        load_runtime_config(path)


def test_duplicate_mount_target_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {first}
        target: /opt/cursor/token
      - source: {second}
        target: /opt/cursor/token
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="more than once"):
        load_runtime_config(path)


def test_fifo_mount_source_is_rejected(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    path = _write(
        tmp_path,
        f"""\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    mounts:
      - source: {fifo}
        target: /opt/cursor/pipe
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="regular file or directory"):
        load_runtime_config(path)


def test_invalid_env_variable_name_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    env_passthrough:
      - not-a-name
verifier:
  image: agentdocs-verifier:local
""",
    )

    with pytest.raises(RuntimeConfigError, match="environment variable"):
        load_runtime_config(path)


def test_missing_passthrough_variable_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTDOCS_MISSING_PROXY", raising=False)
    loaded = load_runtime_config(
        _write(
            tmp_path,
            """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    env_passthrough:
      - AGENTDOCS_MISSING_PROXY
verifier:
  image: agentdocs-verifier:local
""",
        )
    )
    cursor = loaded.agents.cursor
    assert cursor is not None

    with pytest.raises(RuntimeConfigError, match="AGENTDOCS_MISSING_PROXY"):
        require_env_passthrough(cursor.env_passthrough, agent_type="cursor")
