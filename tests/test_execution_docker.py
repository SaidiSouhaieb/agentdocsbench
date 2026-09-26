"""Docker runtime argv, cleanup, and failure boundaries. No daemon is used."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentdocs import AgentExecutableNotFoundError, AgentTimeoutError, CursorAdapter
from agentdocs.agents.codex import CodexAdapter
from agentdocs.execution.docker import (
    DockerExecutionRuntime,
    agent_create_args,
    verifier_create_args,
)
from agentdocs.execution.errors import (
    DockerContainerError,
    DockerDaemonUnavailableError,
    DockerExecutableNotFoundError,
    DockerImageNotFoundError,
)
from agentdocs.execution.runtime_info import runtime_document
from agentdocs.runtime_config import load_runtime_config
from agentdocs.verifier.errors import VerifierExecutableNotFoundError
from tests.docker_script import DockerScript

_PROMPT = "Create a user named Alice."


def _runtime_text(
    *,
    agent: str = "cursor",
    image: str = "agentdocs-cursor:local",
    network: str = "bridge",
    env: str = "",
    mounts: str = "",
) -> str:
    return f"""\
version: 1
backend: docker
agents:
  {agent}:
    image: {image}
    network: {network}
{env}{mounts}verifier:
  image: agentdocs-verifier:local
"""


def _load(tmp_path: Path, text: str) -> DockerExecutionRuntime:
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")
    return DockerExecutionRuntime(load_runtime_config(path))


def _patch(monkeypatch: pytest.MonkeyPatch, script: DockerScript) -> None:
    def which(name: str) -> str | None:
        if name == "docker":
            return "/usr/bin/docker"
        raise AssertionError(f"host executable lookup: {name}")

    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.docker.subprocess.run", script)


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "host-project-should-be-mounted"
    docs = tmp_path / "host-docs-should-not-appear-in-prompt"
    project.mkdir()
    docs.mkdir()
    return project, docs


def test_agent_container_argv_hides_the_verifier_and_keeps_docs_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "cursor-token"
    token.write_text("token-bytes", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    monkeypatch.setenv("SECRET_TOKEN", "super-secret-value")
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(
        tmp_path,
        _runtime_text(
            env="    env_passthrough:\n      - HTTPS_PROXY\n",
            mounts=(
                "    mounts:\n"
                f"      - source: {token}\n"
                "        target: /opt/cursor/token\n"
                "        read_only: true\n"
            ),
        ),
    )
    project, docs = _dirs(tmp_path)
    verifier_host = tmp_path / "verifier-host-secret"

    result = CursorAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert result.exit_code == 0
    assert result.failure is None
    agent_args = script.creates_for("target=/workspace/project")[0]
    joined = " ".join(agent_args)
    assert "--cap-drop" in agent_args and "ALL" in agent_args
    assert "no-new-privileges" in agent_args
    assert "--privileged" not in agent_args
    assert "docker.sock" not in joined
    assert f"{os.getuid()}:{os.getgid()}" in agent_args
    assert "/tmp:rw,nosuid,nodev" in agent_args
    assert "HOME=/tmp/agentdocs-home" in agent_args
    assert agent_args[agent_args.index("--network") + 1] == "bridge"
    assert agent_args[agent_args.index("-w") + 1] == "/workspace/project"
    project_mount = next(item for item in agent_args if "target=/workspace/project" in item)
    docs_mount = next(item for item in agent_args if "target=/workspace/docs" in item)
    assert "readonly" not in project_mount
    assert "readonly" in docs_mount
    assert "/workspace/verifier" not in joined
    assert "verifier-host-secret" not in joined
    prompt = agent_args[-1]
    assert "/workspace/docs" in prompt
    assert str(docs) not in prompt
    assert str(docs) in docs_mount
    assert "--add-dir" in agent_args
    assert agent_args[agent_args.index("--add-dir") + 1] == "/workspace/docs"
    assert "HTTPS_PROXY=http://proxy.example" in agent_args
    assert "SECRET_TOKEN" not in joined
    assert "super-secret-value" not in joined
    assert "target=/opt/cursor/token" in joined
    assert "--entrypoint" in agent_args
    assert agent_args[agent_args.index("--entrypoint") + 1] == "agent"
    script.assert_cleaned()


def test_verifier_container_has_no_docs_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "cursor-token"
    token.write_text("token-bytes", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(
        tmp_path,
        _runtime_text(
            env="    env_passthrough:\n      - HTTPS_PROXY\n",
            mounts=(
                "    mounts:\n"
                f"      - source: {token}\n"
                "        target: /opt/cursor/token\n"
            ),
        ),
    )
    project = tmp_path / "project"
    verifier = tmp_path / "verifier-copy"
    project.mkdir()
    verifier.mkdir()

    completed = runtime.run_verifier(
        ["python3", "check.py"],
        host_project=project,
        host_verifier=verifier,
        timeout_seconds=30,
    )

    assert completed.returncode == 0
    args = script.creates_for("target=/workspace/verifier")[0]
    joined = " ".join(args)
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("-w") + 1] == "/workspace/verifier"
    assert "AGENTDOCS_PROJECT_DIR=/workspace/project" in args
    assert "target=/workspace/docs" not in joined
    assert "target=/opt/cursor/token" not in joined
    assert "HTTPS_PROXY" not in joined
    assert "http://proxy.example" not in joined
    assert args[args.index("--entrypoint") + 1] == "python3"
    assert args[-1] == "check.py"
    assert "--privileged" not in args
    assert "docker.sock" not in joined
    script.assert_cleaned()


def test_builder_matches_the_runtime_mount_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifier"
    args = agent_create_args(
        name="agentdocs-agent-test",
        image="agentdocs-cursor:local",
        command=["agent", "-p", "--add-dir", "/workspace/docs", "task"],
        host_project=project,
        host_docs=docs,
        network="none",
        extra_mounts=[],
        environment={},
    )
    verifier_args = verifier_create_args(
        name="agentdocs-verifier-test",
        image="agentdocs-verifier:local",
        command=["python3", "check.py"],
        host_project=project,
        host_verifier=verifier,
    )

    assert args[args.index("--network") + 1] == "none"
    assert "target=/workspace/verifier" not in " ".join(args)
    assert verifier_args[verifier_args.index("--network") + 1] == "none"
    assert "shell" not in args


def test_cursor_fallback_uses_the_image_when_the_host_has_no_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.missing_executables.add("agent")
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text())
    project, docs = _dirs(tmp_path)

    result = CursorAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert result.exit_code == 0
    agent_args = script.creates_for("target=/workspace/project")[0]
    assert agent_args[agent_args.index("--entrypoint") + 1] == "cursor-agent"
    script.assert_cleaned()


def test_docker_does_not_require_the_host_provider_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    result = CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert result.exit_code == 0
    assert result.failure is None


def test_missing_docker_cli_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", lambda _name: None)
    runtime = _load(tmp_path, _runtime_text())

    with pytest.raises(DockerExecutableNotFoundError, match="not found in PATH"):
        runtime.prepare("cursor")


def test_daemon_unavailable_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.daemon_down = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text())

    with pytest.raises(DockerDaemonUnavailableError):
        runtime.prepare("cursor")
    assert script.created == []


def test_missing_image_is_infrastructure_and_names_manual_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.images.pop("agentdocs-cursor:local")
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text())

    with pytest.raises(DockerImageNotFoundError, match="does not pull or build"):
        runtime.prepare("cursor")


def test_container_start_failure_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.fail_start = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(DockerContainerError, match="did not start"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)
    script.assert_cleaned()


def test_oom_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.oom = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(DockerContainerError, match="OOMKilled"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)
    script.assert_cleaned()


def test_inspect_failure_still_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.fail_inspect = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(DockerContainerError, match="inspect"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)
    script.assert_cleaned()


def test_invalid_inspect_json_still_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.invalid_inspect = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(DockerContainerError, match="inspect"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)
    script.assert_cleaned()


def test_timeout_kills_and_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.timeout = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(AgentTimeoutError, match="Codex did not finish"):
        CodexAdapter(runtime=runtime, timeout_seconds=5).run(_PROMPT, project, docs)

    assert script.killed
    script.assert_cleaned()


def test_rm_failure_after_success_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.fail_rm = True
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(DockerContainerError, match="could not remove"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)


def test_provider_quota_inside_the_container_is_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.agent_exit = 1
    script.agent_stdout = '{"type":"error","message":"You have hit your usage limit."}\n'
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    result = CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert result.exit_code == 1
    assert result.failure is not None
    assert result.failure.kind.value == "quota_or_credit"
    assert result.failure.blocking is True
    script.assert_cleaned()


def test_unmatched_provider_exit_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.agent_exit = 1
    script.agent_stdout = '{"type":"thread.started"}\n'
    script.agent_stderr = "something else failed"
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    result = CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert result.failure is not None
    assert result.failure.kind.value == "unknown_agent_failure"
    assert result.failure.blocking is False


def test_missing_executable_inside_the_image_is_not_an_unknown_agent_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.missing_executables.update({"codex"})
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(agent="codex", image="agentdocs-codex:local"))
    project, docs = _dirs(tmp_path)

    with pytest.raises(AgentExecutableNotFoundError, match="does not install provider CLIs"):
        CodexAdapter(runtime=runtime).run(_PROMPT, project, docs)
    script.assert_cleaned()


def test_missing_verifier_executable_is_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.missing_executables.add("python3")
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text())
    project = tmp_path / "project"
    verifier = tmp_path / "verifier"
    project.mkdir()
    verifier.mkdir()

    with pytest.raises(VerifierExecutableNotFoundError, match="does not install verifier"):
        runtime.run_verifier(
            ["python3", "check.py"],
            host_project=project,
            host_verifier=verifier,
            timeout_seconds=10,
        )
    script.assert_cleaned()


def test_missing_passthrough_stops_before_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTDOCS_MISSING_PROXY", raising=False)
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(
        tmp_path,
        _runtime_text(env="    env_passthrough:\n      - AGENTDOCS_MISSING_PROXY\n"),
    )
    project, docs = _dirs(tmp_path)

    with pytest.raises(Exception, match="AGENTDOCS_MISSING_PROXY"):
        CursorAdapter(runtime=runtime).run(_PROMPT, project, docs)

    assert script.created == []


def test_image_inspect_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text())

    assert runtime.image_id("agentdocs-cursor:local") == "sha256:ccc333"
    assert runtime.image_id("agentdocs-cursor:local") == "sha256:ccc333"

    inspects = [args for args in script.calls if args[:3] == ["docker", "image", "inspect"]]
    assert len(inspects) == 1


def test_provenance_records_images_and_omits_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "cursor-token"
    token.write_text("token-bytes", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(
        tmp_path,
        _runtime_text(
            env="    env_passthrough:\n      - HTTPS_PROXY\n",
            mounts=(
                "    mounts:\n"
                f"      - source: {token}\n"
                "        target: /opt/cursor/token\n"
            ),
        ),
    )

    document = runtime_document(runtime.provenance("cursor"))
    rendered = str(document)

    assert document == {
        "schema_version": 1,
        "backend": "docker",
        "agent": {
            "requested_image": "agentdocs-cursor:local",
            "image_id": "sha256:ccc333",
            "network": "bridge",
        },
        "verifier": {
            "requested_image": "agentdocs-verifier:local",
            "image_id": "sha256:ddd444",
            "network": "none",
        },
    }
    assert "token-bytes" not in rendered
    assert "http://proxy.example" not in rendered
    assert str(token) not in rendered
    assert "runtime.yaml" not in rendered


def test_network_none_is_passed_to_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    _patch(monkeypatch, script)
    runtime = _load(tmp_path, _runtime_text(network="none"))
    project, docs = _dirs(tmp_path)

    CursorAdapter(runtime=runtime).run(_PROMPT, project, docs)

    agent_args = script.creates_for("target=/workspace/project")[0]
    assert agent_args[agent_args.index("--network") + 1] == "none"

