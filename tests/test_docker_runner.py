"""Runner timeline with a mocked Docker runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs import run_task
from agentdocs.agents.codex import CodexAdapter
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig
from agentdocs.execution.docker import DockerExecutionRuntime
from agentdocs.execution.errors import DockerContainerError
from agentdocs.runtime_config import load_runtime_config
from agentdocs.workspace import create_workspace
from tests.docker_script import DockerScript


def _benchmark(tmp_path: Path) -> AgentDocsConfig:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifier-source"
    starter.mkdir()
    docs.mkdir()
    verifier.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("docs\n", encoding="utf-8")
    (verifier / "check.py").write_text("print('unused')\n", encoding="utf-8")
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="codex"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create a user named Alice.",
                verify=VerifyConfig(path=verifier, command="python3 check.py"),
            )
        ],
    )


def _runtime(tmp_path: Path, script: DockerScript, monkeypatch: pytest.MonkeyPatch) -> DockerExecutionRuntime:
    path = tmp_path / "runtime.yaml"
    path.write_text(
        """\
version: 1
backend: docker
agents:
  codex:
    image: agentdocs-codex:local
    network: bridge
verifier:
  image: agentdocs-verifier:local
""",
        encoding="utf-8",
    )

    def which(name: str) -> str | None:
        return "/usr/bin/docker" if name == "docker" else None

    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.docker.subprocess.run", script)
    return DockerExecutionRuntime(load_runtime_config(path))


def test_docker_agent_writes_are_captured_before_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()

    def agent_started(project: Path) -> None:
        (project / "user.txt").write_text("Alice\n", encoding="utf-8")

    def verifier_started(project: Path) -> None:
        (project / "from-verifier.txt").write_text("cache\n", encoding="utf-8")

    script.on_agent_start = agent_started
    script.on_verifier_start = verifier_started
    runtime = _runtime(tmp_path, script, monkeypatch)
    config = _benchmark(tmp_path)

    result = run_task(
        config,
        config.tasks[0],
        agent=CodexAdapter(runtime=runtime),
        runtime=runtime,
    )

    added = [entry.path for entry in result.workspace_changes.added_files]
    assert added == ["user.txt"]
    assert result.passed is True
    assert result.verifier_result.exit_code == 0
    assert result.agent_result.failure is None
    script.assert_cleaned()


def test_verifier_exit_still_decides_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.verifier_exit = 1
    runtime = _runtime(tmp_path, script, monkeypatch)
    config = _benchmark(tmp_path)

    result = run_task(
        config,
        config.tasks[0],
        agent=CodexAdapter(runtime=runtime),
        runtime=runtime,
    )

    assert result.passed is False
    assert result.agent_result.exit_code == 0


def test_blocking_provider_failure_still_records_changes_and_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.agent_exit = 1
    script.agent_stdout = '{"type":"error","message":"You have hit your usage limit."}\n'
    script.on_agent_start = lambda project: (project / "user.txt").write_text(
        "partial\n", encoding="utf-8"
    )
    runtime = _runtime(tmp_path, script, monkeypatch)
    config = _benchmark(tmp_path)

    result = run_task(
        config,
        config.tasks[0],
        agent=CodexAdapter(runtime=runtime),
        runtime=runtime,
    )

    assert result.passed is True
    assert result.agent_result.failure is not None
    assert result.agent_result.failure.kind.value == "quota_or_credit"
    assert [entry.path for entry in result.workspace_changes.added_files] == ["user.txt"]
    assert result.verifier_result.exit_code == 0
    script.assert_cleaned()


def test_docker_infrastructure_error_cleans_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.fail_start = True
    runtime = _runtime(tmp_path, script, monkeypatch)
    config = _benchmark(tmp_path)
    seen: list[Path] = []
    original = create_workspace

    def wrapped(loaded: AgentDocsConfig):
        manager = original(loaded)
        workspace = manager.__enter__()
        seen.append(workspace.root)

        class _Relay:
            def __enter__(self):
                return workspace

            def __exit__(self, exc_type, exc, traceback):
                return manager.__exit__(exc_type, exc, traceback)

        return _Relay()

    monkeypatch.setattr("agentdocs.runner.create_workspace", wrapped)

    with pytest.raises(DockerContainerError, match="did not start"):
        run_task(
            config,
            config.tasks[0],
            agent=CodexAdapter(runtime=runtime),
            runtime=runtime,
        )

    assert seen
    assert not seen[0].exists()
