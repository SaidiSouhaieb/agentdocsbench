from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentdocs import (
    AgentAdapter,
    AgentExecutableNotFoundError,
    AgentOutputError,
    AgentTimeoutError,
    CursorAdapter,
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
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    run: object,
    which: object,
) -> None:
    monkeypatch.setattr("agentdocs.execution.local.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", run)


def test_valid_run_uses_agent_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}
    stdout = '{"type":"assistant"}\n\n{"type":"result"}\n'

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _completed(args, stdout=stdout, stderr="note\n")

    _patch(monkeypatch, fake_run, lambda name: "/usr/bin/agent" if name == "agent" else None)
    adapter = CursorAdapter()
    assert isinstance(adapter, AgentAdapter)
    result = adapter.run(TASK, project, docs)

    assert result.agent == "cursor"
    assert result.stdout == stdout
    assert result.stderr == "note\n"
    assert result.events == ({"type": "assistant"}, {"type": "result"})
    assert result.failure is None
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:4] == ["/usr/bin/agent", "-p", "--output-format", "stream-json"]
    assert args[4:7] == ["--trust", "--add-dir", str(docs.resolve())]
    assert "--force" not in args
    assert "--yolo" not in args
    assert "--model" not in args
    assert "dangerously" not in " ".join(args[:-1])
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == project
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    prompt = args[-1]
    assert TASK in prompt
    assert str(docs.resolve()) in prompt
    assert "current working directory" in prompt
    assert "Do not modify the documentation." in prompt
    assert "Do not commit or push." in prompt
    assert "verifier" not in prompt


def test_falls_back_to_cursor_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def which(name: str) -> str | None:
        if name == "cursor-agent":
            return "/usr/local/bin/cursor-agent"
        return None

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args, stdout='{"type":"ok"}\n')

    _patch(monkeypatch, fake_run, which)
    result = CursorAdapter().run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "/usr/local/bin/cursor-agent"
    assert result.agent == "cursor"


def test_malformed_and_non_object_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    which = lambda name: "/usr/bin/agent" if name == "agent" else None

    def bad_json(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="not-json\n")

    _patch(monkeypatch, bad_json, which)
    with pytest.raises(AgentOutputError, match="Cursor returned invalid JSON on line 1"):
        CursorAdapter().run(TASK, project, docs)

    def non_object(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="1\n")

    _patch(monkeypatch, non_object, which)
    with pytest.raises(AgentOutputError, match="non-object JSON value on line 1"):
        CursorAdapter().run(TASK, project, docs)

    def json_array(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="[]\n")

    _patch(monkeypatch, json_array, which)
    with pytest.raises(AgentOutputError, match="non-object JSON value on line 1"):
        CursorAdapter().run(TASK, project, docs)


def test_nonzero_exit_returns_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, returncode=1, stdout='{"type":"error"}\n')

    _patch(monkeypatch, fake_run, lambda name: "/usr/bin/agent" if name == "agent" else None)
    result = CursorAdapter().run(TASK, project, docs)
    assert result.exit_code == 1
    assert result.events == ({"type": "error"},)
    assert result.failure is not None
    assert result.failure.kind.value == "unknown_agent_failure"
    assert result.failure.blocking is False


def test_timeout_and_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def time_out(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout=600)

    _patch(monkeypatch, time_out, lambda name: "/usr/bin/agent" if name == "agent" else None)
    with pytest.raises(AgentTimeoutError, match="Cursor did not finish"):
        CursorAdapter().run(TASK, project, docs)

    _patch(monkeypatch, time_out, lambda _name: None)
    with pytest.raises(AgentExecutableNotFoundError, match="executable 'agent'"):
        CursorAdapter().run(TASK, project, docs)


def test_path_and_prompt_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    _patch(
        monkeypatch,
        lambda *args, **kwargs: _completed([]),
        lambda name: "/usr/bin/agent" if name == "agent" else None,
    )
    with pytest.raises(ValueError, match="must not be empty"):
        CursorAdapter().run("   ", project, docs)
    with pytest.raises(FileNotFoundError, match="project directory does not exist"):
        CursorAdapter().run(TASK, tmp_path / "missing", docs)
    with pytest.raises(FileNotFoundError, match="docs directory does not exist"):
        CursorAdapter().run(TASK, project, tmp_path / "missing-docs")
    project_file = tmp_path / "file"
    project_file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="project path is not a directory"):
        CursorAdapter().run(TASK, project_file, docs)
    docs_file = tmp_path / "docs-file"
    docs_file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="docs path is not a directory"):
        CursorAdapter().run(TASK, project, docs_file)
    with pytest.raises(ValueError, match="greater than 0"):
        CursorAdapter(timeout_seconds=0)


def test_model_is_one_argv_element(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args, stdout='{"type":"ok"}\n')

    _patch(monkeypatch, fake_run, lambda name: None if name == "agent" else "/usr/local/bin/cursor-agent")
    result = CursorAdapter(model="some-model").run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "/usr/local/bin/cursor-agent"
    assert args[1:4] == ["-p", "--model", "some-model"]
    assert "--trust" in args
    assert "--force" not in args
    assert "--yolo" not in args
    assert args[-1] != "some-model"
    assert result.requested_model == "some-model"
    assert result.resolved_model is None
