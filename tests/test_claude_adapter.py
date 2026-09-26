from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentdocs import (
    AgentExecutableNotFoundError,
    AgentOutputError,
    AgentTimeoutError,
    ClaudeAdapter,
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
    executable: str | None = "/usr/bin/claude",
) -> None:
    monkeypatch.setattr(
        "agentdocs.execution.local.shutil.which",
        lambda _name: executable,
    )
    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", run)


def _capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **completed: object) -> dict[str, object]:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _completed(args, **completed)  # type: ignore[arg-type]

    _patch(monkeypatch, fake_run)
    captured["result"] = ClaudeAdapter().run(TASK, project, docs)
    captured["project"] = project
    captured["docs"] = docs
    return captured


def test_valid_run_returns_claude_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = '{"type":"result","text":"done"}\n\n{"type":"done"}\n'
    captured = _capture(tmp_path, monkeypatch, stdout=stdout, stderr="note\n")
    result = captured["result"]
    assert result.agent == "claude"  # type: ignore[attr-defined]
    assert result.exit_code == 0  # type: ignore[attr-defined]
    assert result.stdout == stdout  # type: ignore[attr-defined]
    assert result.failure is None  # type: ignore[attr-defined]
    assert result.stderr == "note\n"  # type: ignore[attr-defined]
    assert result.events == (  # type: ignore[attr-defined]
        {"type": "result", "text": "done"},
        {"type": "done"},
    )


def test_command_uses_print_mode_and_docs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture(tmp_path, monkeypatch)
    args = captured["args"]
    docs = captured["docs"]
    assert isinstance(args, list)
    assert args[0].endswith("claude")
    assert args[1:8] == [
        "-p",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "acceptEdits",
        "--add-dir",
        str(docs.resolve()),  # type: ignore[attr-defined]
    ]
    assert "--dangerously-skip-permissions" not in args
    assert "--model" not in args
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == captured["project"]
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_prompt_contains_task_and_docs_not_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture(tmp_path, monkeypatch)
    args = captured["args"]
    docs = captured["docs"]
    assert isinstance(args, list)
    prompt = args[-1]
    assert TASK in prompt
    assert str(docs.resolve()) in prompt  # type: ignore[attr-defined]
    assert "current working directory" in prompt
    assert "verifier" not in prompt
    assert "check.py" not in prompt


def test_malformed_and_non_object_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def bad_json(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout='{"ok": true}\nnot-json\n')

    _patch(monkeypatch, bad_json)
    with pytest.raises(AgentOutputError, match="Claude returned invalid JSON on line 2"):
        ClaudeAdapter().run(TASK, project, docs)

    def non_object(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, stdout="[]\n")

    _patch(monkeypatch, non_object)
    with pytest.raises(AgentOutputError, match="non-object JSON value on line 1"):
        ClaudeAdapter().run(TASK, project, docs)


def test_nonzero_exit_returns_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(
        tmp_path,
        monkeypatch,
        returncode=1,
        stdout='{"type":"error"}\n',
    )
    result = captured["result"]
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert result.events == ({"type": "error"},)  # type: ignore[attr-defined]
    assert result.failure is not None  # type: ignore[attr-defined]
    assert result.failure.kind.value == "unknown_agent_failure"  # type: ignore[attr-defined]
    assert result.stdout == '{"type":"error"}\n'  # type: ignore[attr-defined]


def test_timeout_and_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)

    def time_out(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout=600)

    _patch(monkeypatch, time_out)
    with pytest.raises(AgentTimeoutError, match="Claude did not finish"):
        ClaudeAdapter().run(TASK, project, docs)

    _patch(monkeypatch, time_out, executable=None)
    with pytest.raises(AgentExecutableNotFoundError, match="executable 'claude'"):
        ClaudeAdapter().run(TASK, project, docs)


def test_path_and_prompt_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    _patch(monkeypatch, lambda *args, **kwargs: _completed([]))
    with pytest.raises(ValueError, match="must not be empty"):
        ClaudeAdapter().run("   ", project, docs)
    with pytest.raises(FileNotFoundError, match="project directory does not exist"):
        ClaudeAdapter().run(TASK, tmp_path / "missing", docs)
    with pytest.raises(FileNotFoundError, match="docs directory does not exist"):
        ClaudeAdapter().run(TASK, project, tmp_path / "missing-docs")
    project_file = tmp_path / "file"
    project_file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="project path is not a directory"):
        ClaudeAdapter().run(TASK, project_file, docs)
    docs_file = tmp_path / "docs-file"
    docs_file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="docs path is not a directory"):
        ClaudeAdapter().run(TASK, project, docs_file)
    with pytest.raises(ValueError, match="greater than 0"):
        ClaudeAdapter(timeout_seconds=0)


def test_model_is_one_argv_element(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, docs = _dirs(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return _completed(args, stdout='{"type":"result"}\n')

    _patch(monkeypatch, fake_run)
    result = ClaudeAdapter(model="sonnet").run(TASK, project, docs)

    args = captured["args"]
    assert isinstance(args, list)
    assert args[1:4] == ["-p", "--model", "sonnet"]
    assert "--output-format" in args
    assert args[-1] != "sonnet"
    assert result.requested_model == "sonnet"
    assert result.resolved_model is None
