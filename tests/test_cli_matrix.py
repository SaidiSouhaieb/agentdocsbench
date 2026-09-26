from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs import AgentRunResult, TaskRunResult, VerifierResult
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.cli import app
from tests.github_support import github_files, parse_github_output
from agentdocs.matrix import MatrixRunResult, MatrixTargetResult
from agentdocs.matrix_progress import MatrixTargetStarted
from agentdocs.progress import AgentFinished, AgentStarted, TaskStarted, VerifierFinished
from agentdocs.suite import SuiteRunResult

runner = CliRunner()

_CONFIG = """\
version: 1
docs: ./docs
starter: ./starter
agent:
  type: cursor
tasks:
  - id: create_user
    prompt: Create a user named Alice.
    verify:
      path: ./verifier
      command: python3 check.py
  - id: create_project
    prompt: Create the project marker.
    verify:
      path: ./verifier
      command: python3 check.py
"""

_MATRIX = """\
version: 1
targets:
  - id: cursor-default
    agent: cursor
  - id: cursor-named
    agent: cursor
    model: model-a
"""


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "starter").mkdir()
    (root / "verifier").mkdir()
    config = root / "agentdocs.yaml"
    config.write_text(_CONFIG, encoding="utf-8")
    matrix = root / "matrix.yaml"
    matrix.write_text(_MATRIX, encoding="utf-8")
    return config, matrix


def _quota() -> AgentFailureClassification:
    return AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.quota_or_credit,
        blocking=True,
        rule_id="claude.credit_balance_low",
        source="stderr",
    )


def _suite(
    *,
    passed: bool,
    model: str | None,
    failure: AgentFailureClassification | None = None,
) -> SuiteRunResult:
    return SuiteRunResult(
        results=tuple(
            TaskRunResult(
                task_id=task_id,
                agent_result=AgentRunResult(
                    agent="cursor",
                    exit_code=1 if failure is not None else 0,
                    events=({"type": "done"},),
                    stdout="AGENT_STDOUT_MARKER\n",
                    stderr="Credit balance is too low\n" if failure is not None else "AGENT_STDERR_MARKER\n",
                    duration_seconds=1.0,
                    requested_model=model,
                    failure=failure,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0 if passed else 1,
                    stdout="VERIFIER_STDOUT_MARKER\n",
                    stderr="VERIFIER_STDERR_MARKER\n",
                    duration_seconds=0.1,
                ),
                duration_seconds=1.2,
            )
            for task_id in ("create_user", "create_project")
        ),
        duration_seconds=2.4,
    )


def _target(
    target_id: str,
    *,
    model: str | None,
    passed: bool | None,
    failure: AgentFailureClassification | None = None,
) -> MatrixTargetResult:
    if passed is None:
        return MatrixTargetResult(
            target_id=target_id,
            agent_type="cursor",
            requested_model=model,
            suite_result=None,
            error_type="AgentExecutableNotFoundError",
            error_message="cursor-agent was not found",
            duration_seconds=0.2,
        )
    return MatrixTargetResult(
        target_id=target_id,
        agent_type="cursor",
        requested_model=model,
        suite_result=_suite(passed=passed, model=model, failure=failure),
        error_type=None,
        error_message=None,
        duration_seconds=2.4,
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    result: MatrixRunResult,
    *,
    emit: bool = False,
) -> None:
    def fake_run_matrix(_benchmark: object, _matrix: object, **kwargs: object) -> MatrixRunResult:
        if emit:
            progress = kwargs["on_progress"]
            matrix_progress = kwargs["on_matrix_progress"]
            assert callable(progress)
            assert callable(matrix_progress)
            matrix_progress(
                MatrixTargetStarted(
                    target_id="cursor-default",
                    index=1,
                    total=2,
                    agent="cursor",
                    requested_model=None,
                )
            )
            progress(TaskStarted(task_id="create_user", index=1, total=2))
            progress(AgentStarted(task_id="create_user", agent="cursor"))
            finished = result.targets[0]
            assert finished.suite_result is not None
            progress(
                AgentFinished(
                    task_id="create_user",
                    result=finished.suite_result.results[0].agent_result,
                )
            )
            progress(
                VerifierFinished(
                    task_id="create_user",
                    result=finished.suite_result.results[0].verifier_result,
                )
            )
        return result

    monkeypatch.setattr("agentdocs.cli.run_matrix", fake_run_matrix)


def test_matrix_help_exits_zero() -> None:
    result = runner.invoke(app, ["matrix", "--help"])

    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "--matrix" in result.stdout
    assert "--model" not in result.stdout


def test_completed_matrix_exits_zero_and_writes_child_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(
                _target("cursor-default", model=None, passed=True),
                _target("cursor-named", model="model-a", passed=True),
            ),
            duration_seconds=5.0,
        ),
        emit=True,
    )

    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 0
    assert "Target [1/2]: cursor-default" in result.stdout
    assert "Model: provider default" in result.stdout
    assert "PASS" in result.stdout
    assert "winner" not in result.stdout.lower()
    runs = list((config.parent / ".agentdocs" / "runs").iterdir())
    assert len(runs) == 2
    payloads = [
        __import__("json").loads((run / "result.json").read_text(encoding="utf-8"))
        for run in runs
    ]
    assert {payload["schema_version"] for payload in payloads} == {6}
    assert all((run / "tasks").is_dir() for run in runs)
    assert all((run / "benchmark.json").is_file() for run in runs)
    assert all(any((run / "tasks").glob("*/changes.json")) for run in runs)
    assert {payload["requested_model"] for payload in payloads} == {None, "model-a"}
    fingerprints = {payload["benchmark"]["overall_sha256"] for payload in payloads}
    assert len(fingerprints) == 1
    matrices = list((config.parent / ".agentdocs" / "matrices").iterdir())
    assert len(matrices) == 1
    matrix_payload = __import__("json").loads(
        (matrices[0] / "matrix.json").read_text(encoding="utf-8")
    )
    assert matrix_payload["schema_version"] == 4
    assert matrix_payload["benchmark"]["overall_sha256"] == fingerprints.pop()
    assert (matrices[0] / "benchmark.json").is_file()
    assert matrix_payload["targets"][1]["run_id"]


def test_benchmark_failures_exit_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(_target("cursor-default", model=None, passed=False),),
            duration_seconds=2.0,
        ),
    )

    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_target_infrastructure_error_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(
                _target("cursor-default", model=None, passed=True),
                _target("cursor-named", model="model-a", passed=None),
            ),
            duration_seconds=3.0,
        ),
    )

    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 2
    assert "ERROR" in result.stdout
    assert "cursor-agent was not found" in result.stdout
    runs = list((config.parent / ".agentdocs" / "runs").iterdir())
    assert len(runs) == 1


def test_invalid_matrix_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, matrix = _project(tmp_path)
    matrix.write_text("version: 1\ntargets: []\n", encoding="utf-8")
    called = {"n": 0}

    def fail(*_args: object, **_kwargs: object) -> MatrixRunResult:
        called["n"] += 1
        raise AssertionError("matrix should not run")

    monkeypatch.setattr("agentdocs.cli.run_matrix", fail)
    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 2
    assert called["n"] == 0
    assert "At least one matrix target" in result.stderr


def test_quiet_hides_live_target_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(_target("cursor-default", model=None, passed=True),),
            duration_seconds=2.0,
        ),
        emit=True,
    )

    result = runner.invoke(
        app,
        ["matrix", "--quiet", "--config", str(config), "--matrix", str(matrix)],
    )

    assert result.exit_code == 0
    assert "Target [" not in result.stdout
    assert "agent started" not in result.stdout
    assert "PASS" in result.stdout


def test_verbose_and_debug_reuse_the_task_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, matrix = _project(tmp_path)
    outcome = MatrixRunResult(
        targets=(_target("cursor-default", model=None, passed=True),),
        duration_seconds=2.0,
    )
    _patch(monkeypatch, outcome, emit=True)

    verbose = runner.invoke(
        app,
        ["matrix", "--verbose", "--config", str(config), "--matrix", str(matrix)],
    )
    assert verbose.exit_code == 0
    assert "events:" in verbose.stdout
    assert "AGENT_STDOUT_MARKER" not in verbose.stdout

    _patch(monkeypatch, outcome, emit=True)
    debug = runner.invoke(
        app,
        ["matrix", "--debug", "--config", str(config), "--matrix", str(matrix)],
    )
    assert debug.exit_code == 0
    assert "AGENT_STDOUT_MARKER" in debug.stdout
    assert "VERIFIER_STDOUT_MARKER" in debug.stdout


def test_quiet_with_verbose_is_rejected(tmp_path: Path) -> None:
    config, matrix = _project(tmp_path)

    result = runner.invoke(
        app,
        ["matrix", "--quiet", "--verbose", "--config", str(config), "--matrix", str(matrix)],
    )

    assert result.exit_code != 0
    assert "--quiet cannot be combined with --verbose." in result.stdout + result.stderr


def test_provider_error_target_keeps_its_child_run_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(
                _target("cursor-default", model=None, passed=False, failure=_quota()),
                _target("cursor-named", model="model-a", passed=True),
            ),
            duration_seconds=4.0,
        ),
    )

    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 2
    rendered = result.stdout.replace("\n", "")
    assert "PROVIDER_ERROR" in rendered
    assert "quota_or_credit: 2 tasks" in rendered
    assert "Credit balance is too low" not in result.stdout
    runs = list((config.parent / ".agentdocs" / "runs").iterdir())
    assert len(runs) == 2
    matrices = list((config.parent / ".agentdocs" / "matrices").iterdir())
    matrix_text = (matrices[0] / "matrix.json").read_text(encoding="utf-8")
    payload = __import__("json").loads(matrix_text)
    assert payload["schema_version"] == 4
    assert [target["status"] for target in payload["targets"]] == ["provider_error", "pass"]
    assert payload["targets"][0]["run_id"]
    assert payload["targets"][0]["provider_failures"]["quota_or_credit"] == 2


def test_matrix_github_help_is_opt_in() -> None:
    result = runner.invoke(app, ["matrix", "--help"])

    assert result.exit_code == 0
    assert "--github-actions" in result.stdout


def test_matrix_without_github_flag_leaves_github_files_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "step-summary.md"
    output = tmp_path / "output.txt"
    summary.write_text("ORIGINAL\n", encoding="utf-8")
    output.write_text("ORIGINAL\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    def boom() -> None:
        raise AssertionError("github environment was read")

    monkeypatch.setattr("agentdocs.cli.load_github_actions_environment", boom)
    config, matrix = _project(tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(_target("cursor-default", model=None, passed=True),),
            duration_seconds=1.0,
        ),
    )

    result = runner.invoke(app, ["matrix", "--config", str(config), "--matrix", str(matrix)])

    assert result.exit_code == 0
    assert summary.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert output.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_matrix_github_pass_fail_and_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (
        (_target("cursor-default", model=None, passed=True), 0, "pass"),
        (_target("cursor-default", model=None, passed=False), 1, "fail"),
        (
            _target("cursor-default", model=None, passed=False, failure=_quota()),
            2,
            "provider_error",
        ),
    )
    for target, exit_code, status in cases:
        folder = tmp_path / status
        folder.mkdir()
        config, matrix = _project(folder)
        summary, output = github_files(monkeypatch, folder)
        _patch(monkeypatch, MatrixRunResult(targets=(target,), duration_seconds=1.0))

        result = runner.invoke(
            app,
            ["matrix", "--config", str(config), "--matrix", str(matrix), "--github-actions"],
        )

        assert result.exit_code == exit_code
        parsed = parse_github_output(output.read_text(encoding="utf-8"))
        assert parsed["status"] == status
        assert parsed["exit_code"] == str(exit_code)
        assert parsed["runtime_backend"] == "local"
        assert parsed["target_count"] == "1"
        text = summary.read_text(encoding="utf-8")
        assert parsed["artifact_path"] not in text
        assert "AGENT_STDOUT_MARKER" not in text
        assert "VERIFIER_STDERR_MARKER" not in text
        assert "winner" not in text.lower()
        if status == "provider_error":
            assert "Provider execution was incomplete." in text
            assert "quota_or_credit" in text
            assert "Credit balance is too low" not in text
        matrices = list((config.parent / ".agentdocs" / "matrices").iterdir())
        payload = json.loads((matrices[0] / "matrix.json").read_text(encoding="utf-8"))
        assert payload["schema_version"] == 4
        runs = list((config.parent / ".agentdocs" / "runs").iterdir())
        child = json.loads((runs[0] / "result.json").read_text(encoding="utf-8"))
        assert child["schema_version"] == 6
        assert child["runtime"]["schema_version"] == 1
        if status == "provider_error":
            assert child["tasks"][0]["agent"]["failure"]["schema_version"] == 1


def test_matrix_github_infrastructure_target_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, matrix = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(_target("cursor-default", model=None, passed=None),),
            duration_seconds=0.2,
        ),
    )

    result = runner.invoke(
        app,
        ["matrix", "--config", str(config), "--matrix", str(matrix), "--github-actions", "--quiet"],
    )

    assert result.exit_code == 2
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "error"
    assert parsed["error_targets"] == "1"
    assert parsed["pass_targets"] == "0"
    text = summary.read_text(encoding="utf-8")
    assert "**Status:** ERROR" in text
    assert "cursor-agent was not found" not in text


def test_matrix_github_config_error_writes_minimal_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _matrix = _project(tmp_path)
    broken = tmp_path / "broken-matrix.yaml"
    broken.write_text("version: 1\n", encoding="utf-8")
    summary, output = github_files(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        ["matrix", "--config", str(config), "--matrix", str(broken), "--github-actions"],
    )

    assert result.exit_code == 2
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "error"
    assert parsed["exit_code"] == "2"
    assert parsed["matrix_run_id"] == ""
    assert parsed["target_count"] == ""
    text = summary.read_text(encoding="utf-8")
    assert "could not complete the benchmark" in text
    assert str(broken) not in text


@pytest.mark.parametrize("flag", ["--verbose", "--debug"])
def test_matrix_github_verbose_and_debug_still_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    config, matrix = _project(tmp_path)
    _summary, output = github_files(monkeypatch, tmp_path)
    _patch(
        monkeypatch,
        MatrixRunResult(
            targets=(_target("cursor-default", model=None, passed=True),),
            duration_seconds=1.0,
        ),
    )

    result = runner.invoke(
        app,
        ["matrix", "--config", str(config), "--matrix", str(matrix), "--github-actions", flag],
    )

    assert result.exit_code == 0
    assert parse_github_output(output.read_text(encoding="utf-8"))["status"] == "pass"
