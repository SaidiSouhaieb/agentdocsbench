from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs import (
    AgentError,
    AgentExecutableNotFoundError,
    AgentRunResult,
    ArtifactError,
    RunArtifacts,
    SuiteRunResult,
    TaskRunResult,
    VerifierResult,
)
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.cli import app
from tests.github_support import github_files, parse_github_output

runner = CliRunner()

_CONFIG = """\
version: 1
docs: ./docs
starter: ./benchmark/starter
agent:
  type: codex
tasks:
  - id: create_user
    prompt: Create a user named Alice.
    verify:
      path: ./benchmark/verifiers/create_user
      command: python3 check.py
  - id: create_project
    prompt: Create the project marker.
    verify:
      path: ./benchmark/verifiers/create_project
      command: python3 check.py
"""


def _project(tmp_path: Path, contents: str = _CONFIG) -> Path:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "benchmark" / "starter").mkdir(parents=True)
    (root / "benchmark" / "verifiers" / "create_user").mkdir(parents=True)
    (root / "benchmark" / "verifiers" / "create_project").mkdir(parents=True)
    path = root / "agentdocs.yaml"
    path.write_text(contents, encoding="utf-8")
    return path


def _task(
    task_id: str,
    *,
    agent_exit: int,
    verifier_exit: int,
    duration: float = 22.5,
    stderr: str = "",
    failure: object | None = None,
    agent_stdout: str = "",
) -> TaskRunResult:
    return TaskRunResult(
        task_id=task_id,
        agent_result=AgentRunResult(
            agent="codex",
            exit_code=agent_exit,
            events=(),
            stdout=agent_stdout,
            stderr="Credit balance is too low\n" if failure is not None else "",
            duration_seconds=1.0,
            failure=failure,  # type: ignore[arg-type]
        ),
        verifier_result=VerifierResult(
            command=("python3", "check.py"),
            exit_code=verifier_exit,
            stdout="",
            stderr=stderr,
            duration_seconds=0.2,
        ),
        duration_seconds=duration,
    )


def _suite(*tasks: TaskRunResult, duration: float = 43.8) -> SuiteRunResult:
    return SuiteRunResult(results=tasks, duration_seconds=duration)


def _patch_suite(monkeypatch: pytest.MonkeyPatch, result: SuiteRunResult) -> list[object]:
    calls: list[object] = []

    def fake_run_suite(config: object, **_kwargs: object) -> SuiteRunResult:
        calls.append(config)
        return result

    monkeypatch.setattr("agentdocs.cli.run_suite", fake_run_suite)
    return calls


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "test" in result.stdout


def test_test_help_exits_zero() -> None:
    result = runner.invoke(app, ["test", "--help"])

    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "-c" in result.stdout
    assert "--verbose" in result.stdout
    assert "--debug" in result.stdout
    assert "--quiet" in result.stdout
    assert "--model" in result.stdout


def test_successful_suite_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(
        monkeypatch,
        _suite(
            _task("create_user", agent_exit=0, verifier_exit=0, duration=22.5),
            _task("create_project", agent_exit=0, verifier_exit=0, duration=21.3),
        ),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "PASS" in result.stdout
    assert "2 passed, 0 failed" in result.stdout
    assert "Total: 43.8s" in result.stdout


def test_failed_task_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_suite(
        monkeypatch,
        _suite(
            _task("create_user", agent_exit=0, verifier_exit=0),
            _task(
                "create_project",
                agent_exit=0,
                verifier_exit=1,
                stderr="Expected AgentDocsBench, found 'Wrong'",
            ),
        ),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout
    assert "1 passed, 1 failed" in result.stdout
    assert "Expected AgentDocsBench, found 'Wrong'" in result.stdout
    assert "crashed" not in result.stdout.lower()
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_infrastructure_error_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)

    def fail_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        raise AgentExecutableNotFoundError(
            "Codex CLI executable 'codex' was not found in PATH."
        )

    monkeypatch.setattr("agentdocs.cli.run_suite", fail_suite)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "Error: Codex CLI executable 'codex' was not found in PATH." in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_missing_config_exits_two(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    result = runner.invoke(app, ["test", "--config", str(missing)])

    assert result.exit_code == 2
    assert "Error:" in result.stderr
    assert "not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_yaml_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path, "version: [\n")

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "Invalid YAML" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_schema_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path, _CONFIG.replace("version: 1", "version: 2", 1))

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "Unsupported version" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_docs_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path)
    docs = config.parent / "docs"
    docs.rmdir()

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "docs directory does not exist" in result.stderr


def test_missing_starter_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path)
    starter = config.parent / "benchmark" / "starter"
    starter.rmdir()

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "starter directory does not exist" in result.stderr


def test_missing_verifier_exits_two(tmp_path: Path) -> None:
    config = _project(tmp_path)
    verifier = config.parent / "benchmark" / "verifiers" / "create_user"
    verifier.rmdir()

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "Verifier directory for task 'create_user' does not exist" in result.stderr


def test_config_option_uses_supplied_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert len(calls) == 1
    loaded = calls[0]
    assert loaded.tasks[0].id == "create_user"  # type: ignore[attr-defined]
    assert loaded.docs == (config.parent / "docs").resolve()  # type: ignore[attr-defined]
    assert "Config:" in result.stdout


def test_short_config_option_uses_supplied_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(
        monkeypatch,
        _suite(_task("create_project", agent_exit=0, verifier_exit=0)),
    )

    result = runner.invoke(app, ["test", "-c", str(config)])

    assert result.exit_code == 0
    assert len(calls) == 1
    loaded = calls[0]
    assert loaded.docs == (config.parent / "docs").resolve()  # type: ignore[attr-defined]
    assert "Config:" in result.stdout


def test_table_shows_task_results_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_suite(
        monkeypatch,
        _suite(
            _task("create_user", agent_exit=0, verifier_exit=0, duration=22.5),
            _task("create_project", agent_exit=0, verifier_exit=1, duration=18.2),
        ),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert "create_user" in result.stdout
    assert "create_project" in result.stdout
    assert "PASS" in result.stdout
    assert "FAIL" in result.stdout
    assert "22.5s" in result.stdout
    assert "18.2s" in result.stdout
    assert "Agent Exit" in result.stdout
    assert "Verifier Exit" in result.stdout


def test_pass_follows_verifier_when_agent_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=1, verifier_exit=0)),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout
    assert "1 passed, 0 failed" in result.stdout


@pytest.mark.parametrize("agent_type", ["claude", "cursor"])
def test_cli_prints_configured_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_type: str
) -> None:
    config = _project(tmp_path, _CONFIG.replace("type: codex", f"type: {agent_type}", 1))
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))
    _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert f"Agent: {agent_type}" in result.stdout


def test_fail_follows_verifier_when_agent_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=0, verifier_exit=1, stderr="nope")),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout
    assert "PASS" not in result.stdout
    assert "0 passed, 1 failed" in result.stdout


def _patch_artifacts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_write(
        config: object,
        result: object,
        *,
        output_root: Path,
        benchmark_fingerprint: object = None,
    ) -> RunArtifacts:
        calls.append(
            {
                "config": config,
                "result": result,
                "output_root": output_root,
                "benchmark_fingerprint": benchmark_fingerprint,
            }
        )
        root = output_root / "20260924T181900Z-a1b2c3d4"
        return RunArtifacts(
            run_id="20260924T181900Z-a1b2c3d4",
            root=root,
            result_json=root / "result.json",
            summary_markdown=root / "summary.md",
            benchmark_json=root / "benchmark.json",
        )

    monkeypatch.setattr("agentdocs.cli.write_run_artifacts", fake_write)
    return calls


def test_successful_run_writes_default_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    suite = _suite(_task("create_user", agent_exit=0, verifier_exit=0))
    _patch_suite(monkeypatch, suite)
    writes = _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert len(writes) == 1
    assert writes[0]["result"] is suite
    assert writes[0]["output_root"] == config.resolve().parent / ".agentdocs" / "runs"
    assert writes[0]["benchmark_fingerprint"] is not None
    assert "20260924T181900Z-a1b2c3d4" in result.stdout.replace("\n", "")
    assert "Artifacts:" in result.stdout


def test_failed_benchmark_still_writes_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    suite = _suite(_task("create_project", agent_exit=0, verifier_exit=1, stderr="nope"))
    _patch_suite(monkeypatch, suite)
    writes = _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 1
    assert len(writes) == 1
    assert writes[0]["result"] is suite
    assert "Artifacts:" in result.stdout


def test_no_artifacts_skips_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))
    writes = _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config), "--no-artifacts"])

    assert result.exit_code == 0
    assert writes == []
    assert "Artifacts:" not in result.stdout


def test_artifacts_dir_overrides_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    custom = tmp_path / "benchmark-results"
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))
    writes = _patch_artifacts(monkeypatch)

    result = runner.invoke(
        app,
        ["test", "--config", str(config), "--artifacts-dir", str(custom)],
    )

    assert result.exit_code == 0
    assert writes[0]["output_root"] == custom.resolve()


def test_artifact_error_exits_two_after_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    def fail_write(
        _config: object,
        _result: object,
        *,
        output_root: Path,
        benchmark_fingerprint: object = None,
    ) -> RunArtifacts:
        raise ArtifactError(f"Could not create artifact output root {output_root}")

    monkeypatch.setattr("agentdocs.cli.write_run_artifacts", fail_write)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert "PASS" in result.stdout
    assert "Error: Could not create artifact output root" in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_suite_infrastructure_error_does_not_write_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    writes = _patch_artifacts(monkeypatch)

    def fail_suite(_config: object, **_kwargs: object) -> SuiteRunResult:
        raise AgentExecutableNotFoundError(
            "Codex CLI executable 'codex' was not found in PATH."
        )

    monkeypatch.setattr("agentdocs.cli.run_suite", fail_suite)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert writes == []


def test_no_artifacts_with_artifacts_dir_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(
        app,
        [
            "test",
            "--config",
            str(config),
            "--no-artifacts",
            "--artifacts-dir",
            str(tmp_path / "results"),
        ],
    )

    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "--no-artifacts cannot be combined with --artifacts-dir." in combined
    assert calls == []


def _emit_progress(callback: object) -> None:
    from agentdocs.progress import (
        AgentFinished,
        AgentStarted,
        SuiteFinished,
        WorkspaceChangesCaptured,
        SuiteStarted,
        TaskFinished,
        TaskStarted,
        VerifierFinished,
        VerifierStarted,
    )

    assert callable(callback)
    agent = AgentRunResult(
        agent="cursor",
        exit_code=0,
        events=({"type": "done"},),
        stdout="AGENT_STDOUT_MARKER\n",
        stderr="AGENT_STDERR_MARKER\n",
        duration_seconds=14.2,
    )
    verifier = VerifierResult(
        command=("python3", "check.py"),
        exit_code=0,
        stdout="VERIFIER_STDOUT_MARKER\n",
        stderr="VERIFIER_STDERR_MARKER\n",
        duration_seconds=0.1,
    )
    callback(SuiteStarted(total_tasks=2))
    callback(TaskStarted(task_id="create_user", index=1, total=2))
    callback(AgentStarted(task_id="create_user", agent="cursor"))
    callback(AgentFinished(task_id="create_user", result=agent))
    callback(
        WorkspaceChangesCaptured(
            task_id="create_user",
            files_added=1,
            files_modified=0,
            files_deleted=0,
            directories_added=1,
            directories_deleted=0,
            type_changed=0,
            paths=("+ user.txt", "+dir generated"),
        )
    )
    callback(VerifierStarted(task_id="create_user"))
    callback(VerifierFinished(task_id="create_user", result=verifier))
    callback(TaskFinished(task_id="create_user", passed=True, duration_seconds=14.3))
    callback(TaskStarted(task_id="create_project", index=2, total=2))
    callback(AgentStarted(task_id="create_project", agent="cursor"))
    callback(AgentFinished(task_id="create_project", result=agent))
    callback(
        WorkspaceChangesCaptured(
            task_id="create_project",
            files_added=0,
            files_modified=0,
            files_deleted=0,
            directories_added=0,
            directories_deleted=0,
            type_changed=0,
        )
    )
    callback(VerifierStarted(task_id="create_project"))
    callback(VerifierFinished(task_id="create_project", result=verifier))
    callback(TaskFinished(task_id="create_project", passed=True, duration_seconds=18.2))
    callback(SuiteFinished(total=2, passed=2, failed=0, duration_seconds=32.5))


def _patch_progress_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(
        _task("create_user", agent_exit=0, verifier_exit=0, duration=14.3),
        _task("create_project", agent_exit=0, verifier_exit=0, duration=18.2),
    )

    def fake_run_suite(_config: object, **kwargs: object) -> SuiteRunResult:
        _emit_progress(kwargs["on_progress"])
        return suite

    monkeypatch.setattr("agentdocs.cli.run_suite", fake_run_suite)


def test_default_output_shows_task_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_progress_suite(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    text = result.stdout
    assert "[1/2] create_user" in text
    assert "agent started" in text
    assert "agent exit 0" in text
    assert "workspace changes +1 ~0 -0" in text
    assert "user.txt" not in text
    assert "verifier started" in text
    assert "Changes" in text
    assert "+1 ~0 -0" in text
    assert "PASS" in text
    assert "2 passed, 0 failed" in text
    assert "AGENT_STDOUT_MARKER" not in text


def test_quiet_hides_live_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_progress_suite(monkeypatch)

    result = runner.invoke(app, ["test", "--quiet", "--config", str(config)])

    assert result.exit_code == 0
    assert "agent started" not in result.stdout
    assert "workspace changes" not in result.stdout
    assert "[1/2]" not in result.stdout
    assert "Running benchmark..." not in result.stdout
    assert "PASS" in result.stdout
    assert "2 passed, 0 failed" in result.stdout


def test_verbose_shows_agent_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_progress_suite(monkeypatch)

    result = runner.invoke(app, ["test", "--verbose", "--config", str(config)])

    assert result.exit_code == 0
    assert "events: 1" in result.stdout
    assert "stdout lines: 1" in result.stdout
    assert "stderr: present" in result.stdout
    assert "directories: +1 -0" in result.stdout
    assert "type changes: 0" in result.stdout
    assert "+ user.txt" in result.stdout
    assert "AGENT_STDOUT_MARKER" not in result.stdout


def test_debug_shows_captured_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_progress_suite(monkeypatch)

    result = runner.invoke(app, ["test", "--debug", "--config", str(config)])

    text = result.stdout
    assert result.exit_code == 0
    assert "--- cursor stdout: create_user ---" in text
    assert "AGENT_STDOUT_MARKER" in text
    assert "--- end stdout ---" in text
    assert "--- cursor stderr: create_user ---" in text
    assert "AGENT_STDERR_MARKER" in text
    assert "--- verifier stdout: create_user ---" in text
    assert "VERIFIER_STDOUT_MARKER" in text
    assert "--- verifier stderr: create_user ---" in text
    assert "VERIFIER_STDERR_MARKER" in text
    assert "workspace changes +1 ~0 -0" in text
    assert "user.txt" not in text


def test_quiet_with_verbose_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--quiet", "--verbose", "--config", str(config)])

    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "--quiet cannot be combined with --verbose." in combined
    assert calls == []


def test_quiet_with_debug_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--quiet", "--debug", "--config", str(config)])

    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "--quiet cannot be combined with --debug." in combined
    assert calls == []


def test_header_shows_provider_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert "Agent: codex" in result.stdout
    assert "Model: provider default" in result.stdout


def test_cli_model_overrides_yaml_without_editing_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = _CONFIG.replace("type: codex", "type: cursor\n  model: config-model", 1)
    config = _project(tmp_path, contents)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(
        app,
        ["test", "--config", str(config), "--model", "cli-model"],
    )

    assert result.exit_code == 0
    assert "Model: cli-model" in result.stdout
    assert "Model: config-model" not in result.stdout
    loaded = calls[0]
    assert loaded.agent.model == "cli-model"  # type: ignore[attr-defined]
    assert "config-model" in config.read_text(encoding="utf-8")


def test_yaml_model_is_shown_when_cli_does_not_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = _CONFIG.replace("type: codex", "type: cursor\n  model: config-model", 1)
    config = _project(tmp_path, contents)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert "Agent: cursor" in result.stdout
    assert "Model: config-model" in result.stdout


def test_blank_cli_model_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _project(tmp_path)
    calls = _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--config", str(config), "--model", "   "])

    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "Model must not be empty" in combined
    assert calls == []


def test_models_command_reports_each_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentdocs.models import ModelDiscoveryResult, ModelInfo

    def fake_all() -> tuple[ModelDiscoveryResult, ...]:
        return (
            ModelDiscoveryResult(
                agent="codex",
                models=(ModelInfo(id="model-a", display_name="Model A"),),
                status="available",
                message="Local catalog shipped with this Codex CLI.",
            ),
            ModelDiscoveryResult(
                agent="claude",
                models=(),
                status="unsupported",
                message="Run `claude` and use `/model`.",
            ),
            ModelDiscoveryResult(
                agent="cursor",
                models=(),
                status="executable_not_found",
                message="cursor-agent was not found",
            ),
        )

    monkeypatch.setattr("agentdocs.cli.discover_all_models", fake_all)

    result = runner.invoke(app, ["models"])

    assert result.exit_code == 0
    assert "model-a" in result.stdout
    assert "Model A" in result.stdout
    assert "1 model" in result.stdout
    assert "Discovery: unsupported by installed CLI" in result.stdout
    assert "CLI: not installed" in result.stdout
    assert "Codex" in result.stdout
    assert "Claude" in result.stdout
    assert "Cursor" in result.stdout


def test_models_command_can_limit_one_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentdocs.models import ModelDiscoveryResult, ModelInfo

    seen: list[str] = []

    def fake_one(agent: str) -> ModelDiscoveryResult:
        seen.append(agent)
        return ModelDiscoveryResult(
            agent=agent,
            models=(ModelInfo(id="only-codex"),),
            status="available",
        )

    monkeypatch.setattr("agentdocs.cli.discover_models", fake_one)

    result = runner.invoke(app, ["models", "--agent", "codex"])

    assert result.exit_code == 0
    assert seen == ["codex"]
    assert "only-codex" in result.stdout
    assert "Claude" not in result.stdout


def test_models_command_groups_rows_and_marks_default_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentdocs.models import ModelDiscoveryResult, ModelInfo

    def fake_one(_agent: str) -> ModelDiscoveryResult:
        return ModelDiscoveryResult(
            agent="cursor",
            models=(
                ModelInfo(
                    id="auto",
                    display_name="Auto (current, default)",
                    is_default=True,
                ),
                ModelInfo(id="gpt-5.3-codex", display_name="Codex 5.3"),
                ModelInfo(id="gpt-5.3-codex-low", display_name="Codex 5.3  Low\u200b"),
                ModelInfo(id="claude-opus-5-high", display_name="Claude Opus 5"),
            ),
            status="available",
            message="Models reported by the installed Cursor CLI for this account.",
        )

    monkeypatch.setattr("agentdocs.cli.discover_models", fake_one)

    result = runner.invoke(app, ["models", "--agent", "cursor"])

    assert result.exit_code == 0
    assert "4 models. Default: auto" in result.stdout
    assert "(default)" not in result.stdout
    assert "\n  Auto\n" in result.stdout
    assert "\n  GPT\n" in result.stdout
    assert "\n  Claude\n" in result.stdout
    assert "Codex 5.3 Low" in result.stdout
    assert "    auto" in result.stdout
    assert result.stdout.count("  default") == 1


def test_models_command_rejects_unknown_agent() -> None:
    result = runner.invoke(app, ["models", "--agent", "gemini"])

    combined = result.stdout + result.stderr
    assert result.exit_code != 0
    assert "Unsupported agent type 'gemini'" in combined


def _quota() -> AgentFailureClassification:
    return AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.quota_or_credit,
        blocking=True,
        rule_id="claude.credit_balance_low",
        source="stderr",
    )


def _unknown() -> AgentFailureClassification:
    return AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.unknown_agent_failure,
        blocking=False,
        rule_id="unmatched_nonzero_exit",
        source="exit_code",
    )


def test_clean_run_reports_complete_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))
    _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert "Provider execution: complete" in result.stdout


def test_blocking_provider_failure_exits_2_and_hides_raw_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    writes = _patch_artifacts(monkeypatch)
    _patch_suite(
        monkeypatch,
        _suite(
            _task("create_user", agent_exit=1, verifier_exit=0, failure=_quota()),
            _task("create_project", agent_exit=1, verifier_exit=1, failure=_quota()),
        ),
    )

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 2
    assert writes
    assert "quota_or_credit" in result.stdout
    assert "benchmark execution incomplete" in result.stdout
    assert "Credit balance is too low" not in result.stdout
    assert "PASS" in result.stdout
    assert "FAIL" in result.stdout


def test_unknown_failure_follows_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_artifacts(monkeypatch)
    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=1, verifier_exit=0, failure=_unknown())),
    )

    passed = runner.invoke(app, ["test", "--config", str(config)])
    assert passed.exit_code == 0
    assert "unknown_agent_failure" in passed.stdout

    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=1, verifier_exit=1, failure=_unknown())),
    )
    failed = runner.invoke(app, ["test", "--config", str(config)])
    assert failed.exit_code == 1
    assert "unknown_agent_failure" in failed.stdout


def test_quiet_hides_live_classification_and_keeps_the_final_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    _patch_artifacts(monkeypatch)
    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=1, verifier_exit=1, failure=_quota())),
    )

    result = runner.invoke(app, ["test", "--quiet", "--config", str(config)])

    assert result.exit_code == 2
    assert "agent started" not in result.stdout
    assert "quota/credit" not in result.stdout
    assert "quota_or_credit" in result.stdout
    assert "Credit balance is too low" not in result.stdout


def test_github_actions_help_is_opt_in() -> None:
    result = runner.invoke(app, ["test", "--help"])

    assert result.exit_code == 0
    assert "--github-actions" in result.stdout


def test_without_github_flag_leaves_github_files_untouched(
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
    config = _project(tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))
    _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config)])

    assert result.exit_code == 0
    assert summary.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert output.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_github_actions_requires_the_job_environment(tmp_path: Path) -> None:
    config = _project(tmp_path)

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions"])

    assert result.exit_code == 2
    assert "GITHUB_ACTIONS=true" in result.stderr


def test_github_actions_pass_writes_summary_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    monkeypatch.setenv("PROVIDER_TOKEN", "ENV_SECRET_SHOULD_NOT_APPEAR")
    _patch_suite(
        monkeypatch,
        _suite(
            _task(
                "create_user",
                agent_exit=0,
                verifier_exit=0,
                agent_stdout="AGENT_STDOUT_SHOULD_NOT_APPEAR",
            )
        ),
    )
    writes = _patch_artifacts(monkeypatch)

    result = runner.invoke(
        app,
        ["test", "--config", str(config), "--github-actions", "--quiet"],
    )

    assert result.exit_code == 0
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "pass"
    assert parsed["exit_code"] == "0"
    assert parsed["suite_passed"] == "true"
    assert parsed["execution_complete"] == "true"
    assert parsed["runtime_backend"] == "local"
    assert parsed["run_id"] == "20260924T181900Z-a1b2c3d4"
    assert parsed["benchmark_sha256"]
    assert parsed["artifact_path"] == str(writes[0]["output_root"] / parsed["run_id"])
    text = summary.read_text(encoding="utf-8")
    assert "**Status:** PASS" in text
    assert "AGENT_STDOUT_SHOULD_NOT_APPEAR" not in text
    assert "Create a user named Alice." not in text
    assert "ENV_SECRET_SHOULD_NOT_APPEAR" not in text
    assert parsed["artifact_path"] not in text
    assert parsed["run_id"] in text


def test_github_actions_verifier_failure_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=1)))
    _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions"])

    assert result.exit_code == 1
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "fail"
    assert parsed["exit_code"] == "1"
    assert parsed["suite_passed"] == "false"
    assert parsed["execution_complete"] == "true"
    assert "**Status:** FAIL" in summary.read_text(encoding="utf-8")
    assert "Benchmark failed" not in summary.read_text(encoding="utf-8")


def test_github_actions_provider_error_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    _patch_suite(
        monkeypatch,
        _suite(_task("create_user", agent_exit=1, verifier_exit=1, failure=_quota())),
    )
    _patch_artifacts(monkeypatch)

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions", "--verbose"])

    assert result.exit_code == 2
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "provider_error"
    assert parsed["exit_code"] == "2"
    assert parsed["execution_complete"] == "false"
    text = summary.read_text(encoding="utf-8")
    assert "**Status:** PROVIDER_ERROR" in text
    assert "Provider execution was incomplete." in text
    assert "quota_or_credit" in text
    assert "| create_user | FAIL |" in text
    assert "Credit balance is too low" not in text
    assert "Benchmark failed" not in text


def test_github_actions_infrastructure_error_writes_status_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)

    def explode(_config: object, **_kwargs: object) -> SuiteRunResult:
        raise AgentError("SECRET_EXCEPTION /var/lib/agentdocs-host")

    monkeypatch.setattr("agentdocs.cli.run_suite", explode)

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions", "--debug"])

    assert result.exit_code == 2
    assert "SECRET_EXCEPTION" in result.stderr
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "error"
    assert parsed["exit_code"] == "2"
    assert parsed["run_id"] == ""
    assert parsed["suite_passed"] == ""
    assert parsed["execution_complete"] == ""
    assert parsed["runtime_backend"] == "local"
    assert parsed["benchmark_sha256"]
    text = summary.read_text(encoding="utf-8")
    assert "**Status:** ERROR" in text
    assert "could not complete the benchmark" in text
    assert "SECRET_EXCEPTION" not in text
    assert "/var/lib/agentdocs-host" not in text


def test_github_actions_no_artifacts_leaves_run_fields_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    def explode(*_args: object, **_kwargs: object) -> RunArtifacts:
        raise AssertionError("artifacts were written")

    monkeypatch.setattr("agentdocs.cli.write_run_artifacts", explode)

    result = runner.invoke(
        app,
        ["test", "--config", str(config), "--github-actions", "--no-artifacts"],
    )

    assert result.exit_code == 0
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["run_id"] == ""
    assert parsed["artifact_path"] == ""
    assert parsed["status"] == "pass"
    assert "Run id: not recorded" in summary.read_text(encoding="utf-8")
    assert not (config.parent / ".agentdocs").exists()


def test_github_actions_artifact_write_failure_is_an_integration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    def explode(*_args: object, **_kwargs: object) -> RunArtifacts:
        raise ArtifactError("disk full SECRET_ARTIFACT /tmp/host-artifact")

    monkeypatch.setattr("agentdocs.cli.write_run_artifacts", explode)

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions"])

    assert result.exit_code == 2
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "error"
    assert parsed["exit_code"] == "2"
    text = summary.read_text(encoding="utf-8")
    assert "**Status:** ERROR" in text
    assert "**Status:** PASS" not in text
    assert "SECRET_ARTIFACT" not in text


def test_github_actions_report_failure_keeps_written_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing" / "summary.md"))
    output = tmp_path / "output.txt"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    _patch_suite(monkeypatch, _suite(_task("create_user", agent_exit=0, verifier_exit=0)))

    result = runner.invoke(app, ["test", "--config", str(config), "--github-actions"])

    assert result.exit_code == 2
    assert "Could not append to GITHUB_STEP_SUMMARY." in result.stderr
    runs = list((config.parent / ".agentdocs" / "runs").iterdir())
    assert len(runs) == 1
    payload = json.loads((runs[0] / "result.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 6
    assert payload["runtime"]["schema_version"] == 1
    assert "github" not in json.dumps(payload)
    benchmark = json.loads((runs[0] / "benchmark.json").read_text(encoding="utf-8"))
    assert benchmark["schema_version"] == 1
    changes = list((runs[0] / "tasks").rglob("changes.json"))
    assert len(changes) == 1
    assert json.loads(changes[0].read_text(encoding="utf-8"))["schema_version"] == 1
    assert "github" not in payload
    assert output.read_text(encoding="utf-8") == ""
