from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agentdocs.config import VerifyConfig
from agentdocs.verifier import (
    VerifierCommandError,
    VerifierError,
    VerifierExecutableNotFoundError,
    VerifierTimeoutError,
    run_verifier,
)

_CHECK_USER = """\
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
content = (project / "user.txt").read_text(encoding="utf-8").strip()
if content != "Alice":
    print(f"expected Alice, found {content}", file=sys.stderr)
    raise SystemExit(1)
print("user ok")
"""

_PRINT_PROJECT = """\
import os
from pathlib import Path

print(os.environ["AGENTDOCS_PROJECT_DIR"])
print(Path.cwd())
"""

_WRITE_CWD_MARKER = """\
from pathlib import Path

(Path.cwd() / "generated.txt").write_text("created\\n", encoding="utf-8")
print(Path.cwd())
"""

_SLEEP = """\
import os
import time
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
(project / "verifier-cwd.txt").write_text(str(Path.cwd()), encoding="utf-8")
time.sleep(30)
"""


def _project(tmp_path: Path, user: str | None) -> Path:
    project = tmp_path / "workspace" / "project"
    project.mkdir(parents=True)
    if user is not None:
        (project / "user.txt").write_text(user, encoding="utf-8")
    return project


def _verifier(tmp_path: Path, script: str, name: str = "check.py") -> Path:
    source = tmp_path / "verifiers" / "create_user"
    source.mkdir(parents=True)
    (source / name).write_text(script, encoding="utf-8")
    return source


def _verify(source: Path, command: str) -> VerifyConfig:
    return VerifyConfig(path=source, command=command)


def _python(script: str) -> str:
    return shlex.join([sys.executable, script])


def test_successful_verifier_passes(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert result.exit_code == 0
    assert result.passed is True
    assert result.stdout.strip() == "user ok"


def test_failed_verifier_does_not_raise(tmp_path: Path) -> None:
    project = _project(tmp_path, "Bob\n")
    source = _verifier(tmp_path, _CHECK_USER)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert result.exit_code != 0
    assert result.passed is False
    assert "Bob" in result.stderr


def test_stdout_is_captured(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert "user ok" in result.stdout


def test_stderr_is_captured(tmp_path: Path) -> None:
    project = _project(tmp_path, "Bob\n")
    source = _verifier(tmp_path, _CHECK_USER)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert "expected Alice, found Bob" in result.stderr


def test_duration_is_non_negative(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert result.duration_seconds >= 0


def test_project_dir_env_is_provided(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _PRINT_PROJECT, name="show.py")

    result = run_verifier(_verify(source, _python("show.py")), project)

    reported, _cwd = result.stdout.splitlines()
    assert Path(reported) == project.resolve()


def test_project_dir_env_is_the_workspace_project(tmp_path: Path) -> None:
    starter = tmp_path / "original-starter"
    starter.mkdir()
    (starter / "user.txt").write_text("original\n", encoding="utf-8")
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _PRINT_PROJECT, name="show.py")

    result = run_verifier(_verify(source, _python("show.py")), project)

    reported = Path(result.stdout.splitlines()[0])
    assert reported == project.resolve()
    assert reported != starter.resolve()


def test_verifier_runs_from_a_temporary_copy(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _WRITE_CWD_MARKER, name="write_marker.py")

    result = run_verifier(_verify(source, _python("write_marker.py")), project)

    copied_cwd = Path(result.stdout.strip())
    assert copied_cwd.name == "verifier"
    assert copied_cwd.parent.name.startswith("agentdocs-verifier-")
    assert not (source / "generated.txt").exists()


def test_original_verifier_directory_is_not_modified(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _WRITE_CWD_MARKER, name="write_marker.py")
    before = sorted(path.name for path in source.iterdir())

    run_verifier(_verify(source, _python("write_marker.py")), project)

    assert sorted(path.name for path in source.iterdir()) == before
    assert not (source / "generated.txt").exists()


def test_temporary_verifier_directory_is_cleaned_after_success(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _PRINT_PROJECT, name="show.py")

    result = run_verifier(_verify(source, _python("show.py")), project)

    copied_cwd = Path(result.stdout.splitlines()[1])
    assert not copied_cwd.exists()
    assert not copied_cwd.parent.exists()


def test_temporary_verifier_directory_is_cleaned_after_failure(tmp_path: Path) -> None:
    project = _project(tmp_path, "Bob\n")
    script = """\
import os
import sys
from pathlib import Path

print(Path.cwd())
project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
content = (project / "user.txt").read_text(encoding="utf-8").strip()
if content != "Alice":
    print(content, file=sys.stderr)
    raise SystemExit(1)
"""
    source = _verifier(tmp_path, script)

    result = run_verifier(_verify(source, _python("check.py")), project)

    assert result.passed is False
    copied_cwd = Path(result.stdout.strip())
    assert not copied_cwd.exists()


def test_temporary_verifier_directory_is_cleaned_on_nonzero_exit(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    script = """\
from pathlib import Path
import sys

print(Path.cwd())
raise SystemExit(2)
"""
    source = _verifier(tmp_path, script, name="fail.py")

    result = run_verifier(_verify(source, _python("fail.py")), project)

    assert result.exit_code == 2
    assert not Path(result.stdout.strip()).exists()


def test_missing_project_directory_raises(tmp_path: Path) -> None:
    source = _verifier(tmp_path, _CHECK_USER)
    missing = tmp_path / "missing-project"

    with pytest.raises(FileNotFoundError, match="project directory does not exist"):
        run_verifier(_verify(source, _python("check.py")), missing)


def test_project_file_is_rejected(tmp_path: Path) -> None:
    source = _verifier(tmp_path, _CHECK_USER)
    project_file = tmp_path / "project-file"
    project_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="project path is not a directory"):
        run_verifier(_verify(source, _python("check.py")), project_file)


def test_missing_verifier_directory_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    missing = tmp_path / "missing-verifier"
    verify = VerifyConfig.model_construct(path=missing, command=_python("check.py"))

    with pytest.raises(FileNotFoundError, match="Verifier directory does not exist"):
        run_verifier(verify, project)


def test_verifier_file_is_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    verifier_file = tmp_path / "verifier-file"
    verifier_file.write_text("not a directory", encoding="utf-8")
    verify = VerifyConfig.model_construct(
        path=verifier_file,
        command=_python("check.py"),
    )

    with pytest.raises(NotADirectoryError, match="Verifier path is not a directory"):
        run_verifier(verify, project)


def test_symlink_inside_verifier_is_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)
    target = tmp_path / "outside.py"
    target.write_text("print('nope')\n", encoding="utf-8")
    link = source / "nested" / "link.py"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"This platform cannot create symlinks: {exc}")

    with pytest.raises(VerifierError, match="Symbolic links are not supported") as exc_info:
        run_verifier(_verify(source, _python("check.py")), project)

    assert str(link) in str(exc_info.value)


def test_empty_command_is_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)
    verify = VerifyConfig.model_construct(path=source, command="   ")

    with pytest.raises(VerifierCommandError, match="did not contain an executable"):
        run_verifier(verify, project)


def test_malformed_command_quoting_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    with pytest.raises(VerifierCommandError, match="could not be parsed"):
        run_verifier(_verify(source, 'python "unterminated'), project)


def test_missing_executable_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    with pytest.raises(
        VerifierExecutableNotFoundError,
        match="Verifier executable 'imaginary-test-runner' was not found",
    ):
        run_verifier(_verify(source, "imaginary-test-runner check.py"), project)


def test_timeout_raises_verifier_timeout_error(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _SLEEP, name="sleep.py")

    with pytest.raises(VerifierTimeoutError, match="0.2 seconds") as exc_info:
        run_verifier(
            _verify(source, _python("sleep.py")),
            project,
            timeout_seconds=0.2,
        )

    assert str(project.resolve()) in str(exc_info.value)
    copied_cwd = Path((project / "verifier-cwd.txt").read_text(encoding="utf-8"))
    assert not copied_cwd.exists()
    assert not copied_cwd.parent.exists()


def test_timeout_must_be_positive(tmp_path: Path) -> None:
    project = _project(tmp_path, "Alice\n")
    source = _verifier(tmp_path, _CHECK_USER)

    with pytest.raises(ValueError, match="timeout_seconds must be greater than 0"):
        run_verifier(_verify(source, _python("check.py")), project, timeout_seconds=0)


def test_subprocess_does_not_use_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, None)
    source = _verifier(tmp_path, _CHECK_USER)
    captured: dict[str, object] = {}

    def fake_run(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agentdocs.execution.local.subprocess.run", fake_run)

    result = run_verifier(_verify(source, _python("check.py")), project)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["cwd"].name == "verifier"
    assert result.passed is True
    assert result.command[0] == sys.executable
