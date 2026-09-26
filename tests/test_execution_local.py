"""Local execution does not call Docker."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agentdocs import AgentExecutableNotFoundError, CursorAdapter, run_task
from agentdocs.agents.base import AgentAdapter, AgentRunResult
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig
from agentdocs.execution.local import LocalExecutionRuntime


class _WritingAgent(AgentAdapter):
    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        del prompt, docs_dir
        (project_dir / "user.txt").write_text("Alice\n", encoding="utf-8")
        return AgentRunResult(
            agent="fake",
            exit_code=0,
            events=(),
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )


def test_local_suite_does_not_look_up_or_invoke_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_which = __import__("shutil").which
    real_run = subprocess.run

    def which(name: str) -> str | None:
        calls.append(name)
        if name == "docker":
            raise AssertionError("local execution looked up docker")
        return real_which(name)

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "docker":
            raise AssertionError("local execution invoked docker")
        return real_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.local.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", which)
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", run)

    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifier"
    starter.mkdir()
    docs.mkdir()
    verifier.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("docs\n", encoding="utf-8")
    (verifier / "check.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "project = Path(os.environ['AGENTDOCS_PROJECT_DIR'])\n"
        "assert (project / 'user.txt').read_text() == 'Alice\\n'\n",
        encoding="utf-8",
    )
    config = AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="cursor"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create Alice.",
                verify=VerifyConfig(
                    path=verifier,
                    command=shlex.join([sys.executable, "check.py"]),
                ),
            )
        ],
    )

    result = run_task(config, config.tasks[0], agent=_WritingAgent())

    assert result.passed is True
    assert result.workspace_changes.added_files[0].path == "user.txt"
    assert "docker" not in calls
    assert LocalExecutionRuntime().provenance("cursor").backend == "local"


def test_local_missing_executable_does_not_call_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    docs = tmp_path / "docs"
    project.mkdir()
    docs.mkdir()

    def which(name: str) -> str | None:
        if name == "docker":
            raise AssertionError("docker lookup")
        return None

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr("agentdocs.execution.local.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", run)

    with pytest.raises(AgentExecutableNotFoundError, match="not found in PATH"):
        CursorAdapter().run("Create a user named Alice.", project, docs)
