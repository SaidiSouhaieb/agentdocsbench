from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentdocs import (
    AgentExecutableNotFoundError,
    AgentOutputError,
    AgentTimeoutError,
    CodexAdapter,
)

TASK = "Create a user named Alice."


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    docs = tmp_path / "docs"
    project.mkdir()
    docs.mkdir()
    return project, docs


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args,
        returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _patch_codex(
    monkeypatch: pytest.MonkeyPatch,
    run: object,
    executable: str | None = "/usr/bin/codex",
) -> None:
    monkeypatch.setattr(
        "agentdocs.execution.local.shutil.which",
        lambda _name: executable,
    )
    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", run)


def test_codex_command_is_constructed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _completed(args, stdout='{"type":"thread.started"}\n')

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert args[0].endswith("codex")
    assert args[1:7] == [
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
    ]
    assert "--full-auto" not in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert "--yolo" not in args
    assert "--model" not in args


def test_subprocess_uses_project_directory_as_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == project


def test_subprocess_does_not_use_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False


def test_task_prompt_is_included(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert TASK in args[-1]


def test_docs_absolute_path_is_included_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert str(docs.resolve()) in args[-1]
    assert "--add-dir" not in args


def test_successful_jsonl_is_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    stdout = (
        '{"type":"thread.started","thread_id":"abc"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}\n'
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout=stdout, stderr="warning\n")

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.events == (
        {"type": "thread.started", "thread_id": "abc"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Done"},
        },
    )
    assert result.failure is None
    assert result.stderr == "warning\n"


def test_stdout_and_stderr_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    stdout = '{"type":"thread.started"}\n'
    stderr = "codex notice\n"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout=stdout, stderr=stderr)

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.stdout == stdout
    assert result.stderr == stderr


def test_exit_code_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, returncode=0, stdout='{"type":"done"}\n')

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.exit_code == 0
    assert result.agent == "codex"


def test_duration_is_non_negative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.duration_seconds >= 0


def test_nonzero_exit_returns_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, returncode=3, stdout='{"type":"error"}\n', stderr="failed")

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.exit_code == 3
    assert result.events == ({"type": "error"},)
    assert result.stderr == "failed"
    assert result.failure is not None
    assert result.failure.kind.value == "unknown_agent_failure"
    assert result.failure.blocking is False


def test_missing_codex_executable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run, executable=None)

    with pytest.raises(AgentExecutableNotFoundError, match="not found in PATH"):
        CodexAdapter().run(TASK, project, docs)


def test_timeout_raises_agent_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])

    _patch_codex(monkeypatch, fake_run)

    with pytest.raises(AgentTimeoutError, match="600 seconds") as exc_info:
        CodexAdapter().run(TASK, project, docs)

    assert str(project) in str(exc_info.value)


def test_invalid_jsonl_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    stdout = '{"type":"thread.started"}\nthis is not json\n'

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout=stdout)

    _patch_codex(monkeypatch, fake_run)

    with pytest.raises(AgentOutputError, match="line 2"):
        CodexAdapter().run(TASK, project, docs)


def test_empty_jsonl_lines_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    stdout = '\n{"type":"a"}\n\n{"type":"b"}\n'

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout=stdout)

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter().run(TASK, project, docs)

    assert result.events == ({"type": "a"}, {"type": "b"})


def test_empty_prompt_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run)

    with pytest.raises(ValueError, match="Task prompt must not be empty"):
        CodexAdapter().run("   ", project, docs)


def test_missing_project_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project, docs = _dirs(tmp_path)
    missing = tmp_path / "missing-project"

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run)

    with pytest.raises(FileNotFoundError, match="project directory does not exist"):
        CodexAdapter().run(TASK, missing, docs)


def test_missing_docs_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _docs = _dirs(tmp_path)
    missing = tmp_path / "missing-docs"

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run)

    with pytest.raises(FileNotFoundError, match="docs directory does not exist"):
        CodexAdapter().run(TASK, project, missing)


def test_project_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    project_file = tmp_path / "project-file"
    project_file.write_text("not a directory", encoding="utf-8")

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run)

    with pytest.raises(NotADirectoryError, match="project path is not a directory"):
        CodexAdapter().run(TASK, project_file, docs)
    assert project.is_dir()


def test_docs_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _docs = _dirs(tmp_path)
    docs_file = tmp_path / "docs-file"
    docs_file.write_text("not a directory", encoding="utf-8")

    def fail_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run should not be called")

    _patch_codex(monkeypatch, fail_run)

    with pytest.raises(NotADirectoryError, match="docs path is not a directory"):
        CodexAdapter().run(TASK, project, docs_file)


def test_prompt_says_not_to_modify_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert "Do not modify the documentation." in args[-1]


def test_prompt_says_to_work_inside_the_current_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args)

    _patch_codex(monkeypatch, fake_run)
    CodexAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert "Modify only the project in your current working directory." in args[-1]


def test_model_is_one_argv_element(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args, stdout='{"type":"thread.started"}\n')

    _patch_codex(monkeypatch, fake_run)
    result = CodexAdapter(model="model-a").run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert args[1:4] == ["exec", "--model", "model-a"]
    assert args[4:9] == [
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
    ]
    assert args[-1] != "model-a"
    assert result.requested_model == "model-a"
    assert result.resolved_model is None
