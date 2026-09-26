from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from agentdocs.agents.base import AgentRunResult
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.artifacts import _SCHEMA_VERSION as RESULT_SCHEMA_VERSION
from agentdocs.execution.runtime_info import RUNTIME_SCHEMA_VERSION, DockerImageInfo, RuntimeInfo
from agentdocs.fingerprint import FINGERPRINT_SCHEMA_VERSION
from agentdocs.github_actions import (
    MATRIX_OUTPUT_KEYS,
    SUITE_OUTPUT_KEYS,
    GitHubActionsEnvironment,
    GitHubActionsError,
    append_github_output,
    append_step_summary,
    load_github_actions_environment,
    matrix_ci_status,
    publish_matrix_infrastructure_error,
    publish_matrix_report,
    publish_suite_infrastructure_error,
    publish_suite_report,
    render_matrix_summary,
    render_suite_summary,
    suite_ci_status,
)
from agentdocs.markdown_text import markdown_table_cell
from agentdocs.matrix import MatrixRunResult, MatrixTargetResult, target_status
from agentdocs.matrix_artifacts import _SCHEMA_VERSION as MATRIX_SCHEMA_VERSION
from agentdocs.runner import TaskRunResult
from agentdocs.suite import SuiteRunResult
from agentdocs.verifier.result import VerifierResult
from agentdocs.workspace_changes import CHANGES_SCHEMA_VERSION, AddedFile, empty_workspace_changes
from tests.github_support import github_files, parse_github_output

_PROMPT = "PROMPT_TEXT_SHOULD_NOT_APPEAR"
_STDOUT = "AGENT_STDOUT_SHOULD_NOT_APPEAR"
_STDERR = "AGENT_STDERR_SHOULD_NOT_APPEAR"
_VERIFIER_STDOUT = "VERIFIER_STDOUT_SHOULD_NOT_APPEAR"
_VERIFIER_STDERR = "VERIFIER_STDERR_SHOULD_NOT_APPEAR"
_ENV_SECRET = "ENV_SECRET_SHOULD_NOT_APPEAR"
_SHA = "a" * 64
_HOST_PATH = "/tmp/host-artifact-root/should-not-appear"


def test_flag_environment_requires_github_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(GitHubActionsError, match="GITHUB_ACTIONS=true"):
        load_github_actions_environment()


def test_flag_environment_rejects_other_github_actions_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    with pytest.raises(GitHubActionsError, match="GITHUB_ACTIONS=true"):
        load_github_actions_environment()


def test_flag_environment_requires_step_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    with pytest.raises(GitHubActionsError, match="GITHUB_STEP_SUMMARY"):
        load_github_actions_environment()


def test_flag_environment_requires_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    with pytest.raises(GitHubActionsError, match="GITHUB_OUTPUT"):
        load_github_actions_environment()


def test_valid_environment_returns_the_given_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary, output = github_files(monkeypatch, tmp_path)
    summary.write_text("kept\n", encoding="utf-8")
    output.write_text("kept\n", encoding="utf-8")

    loaded = load_github_actions_environment()

    assert loaded == GitHubActionsEnvironment(summary_path=summary, output_path=output)
    assert summary.read_text(encoding="utf-8") == "kept\n"
    assert output.read_text(encoding="utf-8") == "kept\n"
    assert not (tmp_path / "invented-summary.md").exists()


def test_summary_append_failure_names_the_summary_file(tmp_path: Path) -> None:
    directory = tmp_path / "summary.md"
    directory.mkdir()
    with pytest.raises(GitHubActionsError, match="GITHUB_STEP_SUMMARY"):
        append_step_summary(directory, "# AgentDocsBench\n")


def test_output_append_failure_names_the_output_file(tmp_path: Path) -> None:
    directory = tmp_path / "output.txt"
    directory.mkdir()
    with pytest.raises(GitHubActionsError, match="GITHUB_OUTPUT"):
        append_github_output(directory, _suite_outputs(), SUITE_OUTPUT_KEYS)


def test_summary_and_output_append_to_existing_content(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    output = tmp_path / "output.txt"
    summary.write_text("earlier step\n", encoding="utf-8")
    output.write_text("earlier=1\n", encoding="utf-8")

    append_step_summary(summary, "# AgentDocsBench\n")
    append_github_output(output, _suite_outputs(), SUITE_OUTPUT_KEYS)

    assert summary.read_text(encoding="utf-8").startswith("earlier step\n")
    assert "# AgentDocsBench" in summary.read_text(encoding="utf-8")
    parsed = parse_github_output(output.read_text(encoding="utf-8").split("earlier=1\n", 1)[1])
    assert parsed["status"] == "pass"


def test_output_writer_preserves_spaces_percent_unicode_and_newlines(tmp_path: Path) -> None:
    output = tmp_path / "output.txt"
    values = _suite_outputs()
    values["artifact_path"] = "dir with spaces/%/café\nsecond line"
    values["run_id"] = ""

    append_github_output(output, values, SUITE_OUTPUT_KEYS)

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["artifact_path"] == "dir with spaces/%/café\nsecond line"
    assert parsed["run_id"] == ""
    assert "null" not in output.read_text(encoding="utf-8")
    assert "::set-output" not in output.read_text(encoding="utf-8")


def test_output_delimiter_is_regenerated_when_it_collides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tokens = iter(("inside", "inside", "inside", "inside", "safe", "safe", "safe", "safe", "safe"))

    class _Token:
        def __init__(self, hex_value: str) -> None:
            self.hex = hex_value

    monkeypatch.setattr(
        "agentdocs.github_actions.uuid.uuid4",
        lambda: _Token(next(tokens)),
    )
    output = tmp_path / "output.txt"
    values = _suite_outputs()
    values["artifact_path"] = "agentdocs_inside"

    append_github_output(output, values, SUITE_OUTPUT_KEYS)

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["artifact_path"] == "agentdocs_inside"


def test_suite_statuses_and_outputs(tmp_path: Path) -> None:
    cases = (
        (_suite(passed=True), "pass", 0, "true", "true"),
        (_suite(passed=False), "fail", 1, "false", "true"),
        (_suite(passed=False, failure=_quota()), "provider_error", 2, "false", "false"),
    )
    for result, status, exit_code, suite_passed, execution_complete in cases:
        summary, output = github_files_fresh(tmp_path, status)
        markdown = publish_suite_report(
            GitHubActionsEnvironment(summary, output),
            result,
            agent_type="cursor",
            model="composer-2.5",
            benchmark_sha256=_SHA,
            exit_code=exit_code,
            run_id="20260926T141306Z-1f8d1a46",
            artifact_path=f"{_HOST_PATH}/runs/20260926T141306Z-1f8d1a46",
        )
        parsed = parse_github_output(output.read_text(encoding="utf-8"))
        assert suite_ci_status(result) == status
        assert parsed["status"] == status
        assert parsed["exit_code"] == str(exit_code)
        assert parsed["suite_passed"] == suite_passed
        assert parsed["execution_complete"] == execution_complete
        assert parsed["runtime_backend"] == "local"
        assert parsed["benchmark_sha256"] == _SHA
        assert parsed["run_id"] == "20260926T141306Z-1f8d1a46"
        assert _HOST_PATH not in markdown
        assert _HOST_PATH not in summary.read_text(encoding="utf-8")


def test_infrastructure_error_outputs_are_empty_where_no_run_exists(tmp_path: Path) -> None:
    summary, output = github_files_fresh(tmp_path, "infra")
    markdown = publish_suite_infrastructure_error(
        GitHubActionsEnvironment(summary, output),
        benchmark_sha256=_SHA,
        runtime_backend="docker",
    )
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed == {
        "status": "error",
        "exit_code": "2",
        "run_id": "",
        "artifact_path": "",
        "benchmark_sha256": _SHA,
        "suite_passed": "",
        "execution_complete": "",
        "runtime_backend": "docker",
    }
    assert "**Status:** ERROR" in markdown
    assert "could not complete the benchmark" in markdown
    assert "SECRET_EXCEPTION" not in markdown


def test_pass_summary_shows_counts_model_and_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_TOKEN", _ENV_SECRET)
    result = _suite(passed=True, model="composer-2.5", changes=1)
    markdown = render_suite_summary(
        result,
        agent_type="cursor",
        model="composer-2.5",
        benchmark_sha256=_SHA,
        run_id="run-1",
        status="pass",
    )
    assert "**Status:** PASS" in markdown
    assert "**Agent:** cursor" in markdown
    assert "**Model:** composer-2.5" in markdown
    assert "**Runtime:** local" in markdown
    assert f"`sha256:{_SHA}`" in markdown
    assert "| Tasks | 1 |" in markdown
    assert "| Passed | 1 |" in markdown
    assert "| Failed | 0 |" in markdown
    assert "| Execution complete | yes |" in markdown
    assert "| Blocking provider failures | 0 |" in markdown
    assert "| Unknown agent failures | 0 |" in markdown
    assert "| create_user | PASS | 0 | — | +1 ~0 -0 |" in markdown
    assert "Run id: `run-1`" in markdown
    _assert_private_text_absent(markdown)


def test_fail_summary_keeps_verifier_failure_distinct() -> None:
    markdown = render_suite_summary(
        _suite(passed=False),
        agent_type="codex",
        model=None,
        benchmark_sha256=_SHA,
        run_id="",
        status="fail",
    )
    assert "**Status:** FAIL" in markdown
    assert "Verifier checks failed." in markdown
    assert "**Model:** provider default" in markdown
    assert "| create_user | FAIL | 0 | — | +0 ~0 -0 |" in markdown
    assert "Run id: not recorded" in markdown
    assert "Provider execution was incomplete." not in markdown
    assert "Benchmark failed" not in markdown


def test_provider_error_summary_shows_verifier_and_provider_facts() -> None:
    markdown = render_suite_summary(
        _suite(passed=False, failure=_quota()),
        agent_type="claude",
        model=None,
        benchmark_sha256=_SHA,
        run_id="run-2",
        status="provider_error",
    )
    assert "**Status:** PROVIDER_ERROR" in markdown
    assert "Provider execution was incomplete." in markdown
    assert "| create_user | FAIL | 1 | quota_or_credit | +0 ~0 -0 |" in markdown
    assert "quota_or_credit: 1 task" in markdown
    assert "| Execution complete | no |" in markdown
    assert "| Blocking provider failures | 1 |" in markdown
    assert "Benchmark failed" not in markdown
    _assert_private_text_absent(markdown)


def test_unknown_agent_failure_stays_a_pass_when_the_verifier_passes() -> None:
    result = _suite(passed=True, failure=_unknown(), changes=1)
    assert suite_ci_status(result) == "pass"
    markdown = render_suite_summary(
        result,
        agent_type="cursor",
        model=None,
        benchmark_sha256=_SHA,
        run_id="run-3",
        status="pass",
    )
    assert "**Status:** PASS" in markdown
    assert "| Unknown agent failures | 1 |" in markdown
    assert "| create_user | PASS | 1 | unknown_agent_failure | +1 ~0 -0 |" in markdown
    assert "Provider execution was incomplete." not in markdown


def test_docker_summary_shows_requested_images_and_networks() -> None:
    runtime = RuntimeInfo(
        schema_version=RUNTIME_SCHEMA_VERSION,
        backend="docker",
        agent=DockerImageInfo("my-cursor:local", "sha256:agent", "bridge"),
        verifier=DockerImageInfo("my-verifier:local", "sha256:verifier", "none"),
    )
    markdown = render_suite_summary(
        _suite(passed=True, runtime=runtime),
        agent_type="cursor",
        model=None,
        benchmark_sha256=_SHA,
        run_id="run-4",
        status="pass",
    )
    assert "**Runtime:** docker" in markdown
    assert "**Agent image:** `my-cursor:local`" in markdown
    assert "**Agent network:** bridge" in markdown
    assert "**Verifier image:** `my-verifier:local`" in markdown
    assert "**Verifier network:** none" in markdown
    assert "sha256:agent" not in markdown
    assert "/var/run/docker.sock" not in markdown
    _assert_private_text_absent(markdown)


def test_matrix_status_precedence() -> None:
    assert matrix_ci_status(_matrix(("pass",))) == "pass"
    assert matrix_ci_status(_matrix(("pass", "fail"))) == "fail"
    assert matrix_ci_status(_matrix(("fail", "provider_error"))) == "provider_error"
    assert matrix_ci_status(_matrix(("fail", "provider_error", "error"))) == "error"
    assert matrix_ci_status(_matrix(("error",))) == "error"


def test_matrix_summaries_are_factual(tmp_path: Path) -> None:
    passing = _matrix(("pass", "pass"), backend="docker", image="my-cursor:local")
    failing = _matrix(("pass", "fail"))
    blocked = _matrix(("provider_error", "pass"))
    broken = _matrix(("error", "pass"))
    mixed = _matrix(("fail", "provider_error", "error"), backend="docker", image="registry.example/agent:latest")

    for index, (result, status, exit_code) in enumerate(
        (
            (passing, "pass", 0),
            (failing, "fail", 1),
            (blocked, "provider_error", 2),
            (broken, "error", 2),
            (mixed, "error", 2),
        )
    ):
        summary, output = github_files_fresh(tmp_path, f"{index}-{status}")
        markdown = publish_matrix_report(
            GitHubActionsEnvironment(summary, output),
            result,
            benchmark_sha256=_SHA,
            exit_code=exit_code,
            matrix_run_id="matrix-1",
            artifact_path=f"{_HOST_PATH}/matrices/matrix-1",
            run_ids={"alpha": "child-alpha", "beta": "child-beta"},
        )
        parsed = parse_github_output(output.read_text(encoding="utf-8"))
        assert tuple(parsed) == MATRIX_OUTPUT_KEYS
        assert parsed["status"] == status
        assert parsed["exit_code"] == str(exit_code)
        assert parsed["matrix_run_id"] == "matrix-1"
        assert parsed["benchmark_sha256"] == _SHA
        assert parsed["runtime_backend"] == result.runtime_backend
        assert parsed["target_count"] == str(len(result.targets))
        counts = {"pass": 0, "fail": 0, "provider_error": 0, "error": 0}
        for target in result.targets:
            counts[target_status(target)] += 1
        assert parsed["pass_targets"] == str(counts["pass"])
        assert parsed["fail_targets"] == str(counts["fail"])
        assert parsed["provider_error_targets"] == str(counts["provider_error"])
        assert parsed["error_targets"] == str(counts["error"])
        assert _HOST_PATH not in markdown
        assert "winner" not in markdown.lower()
        assert "leaderboard" not in markdown.lower()
        assert "child-alpha" in markdown
        assert "Matrix run id: `matrix-1`" in markdown
        _assert_private_text_absent(markdown)
        assert "SECRET_INFRA" not in markdown

    blocked_text = render_matrix_summary(
        blocked,
        benchmark_sha256=_SHA,
        matrix_run_id="matrix-1",
        run_ids={},
        status="provider_error",
    )
    assert "Provider execution was incomplete." in blocked_text
    assert "quota_or_credit:" in blocked_text
    assert "Benchmark failed" not in blocked_text
    docker_text = render_matrix_summary(
        passing,
        benchmark_sha256=_SHA,
        matrix_run_id="matrix-1",
        run_ids={},
        status="pass",
    )
    assert "**Runtime:** docker" in docker_text
    assert "`my-cursor:local`" in docker_text


def test_matrix_infrastructure_error_omits_exception_text(tmp_path: Path) -> None:
    summary, output = github_files_fresh(tmp_path, "matrix-infra")
    markdown = publish_matrix_infrastructure_error(
        GitHubActionsEnvironment(summary, output),
    )
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "error"
    assert parsed["exit_code"] == "2"
    assert parsed["matrix_run_id"] == ""
    assert parsed["target_count"] == ""
    assert parsed["pass_targets"] == ""
    assert "could not complete the benchmark" in markdown
    assert "SECRET_EXCEPTION /opt/secret" not in markdown


def test_markdown_cells_keep_the_table_shape() -> None:
    task_id = "id | with `tick`\nand <tag> café \x01"
    cell = markdown_table_cell(task_id)
    result = _suite(passed=True, task_id=task_id)
    markdown = render_suite_summary(
        result,
        agent_type="cursor",
        model="model`x`<y>",
        benchmark_sha256=_SHA,
        run_id="run|1",
        status="pass",
    )
    assert "\n" not in cell
    assert "|" not in cell.replace("\\|", "")
    assert "`" not in cell
    assert "<" not in cell
    assert ">" not in cell
    assert "café" in cell
    assert "\x01" not in cell
    rows = [line for line in markdown.splitlines() if line.startswith("| id")]
    assert len(rows) == 1
    assert _unescaped_pipes(rows[0]) == 6
    assert "`" not in markdown.split("**Model:** ", 1)[1].split("\n", 1)[0]


def test_schema_versions_are_unchanged() -> None:
    assert RESULT_SCHEMA_VERSION == 6
    assert MATRIX_SCHEMA_VERSION == 4
    assert FINGERPRINT_SCHEMA_VERSION == 1
    assert CHANGES_SCHEMA_VERSION == 1
    assert RUNTIME_SCHEMA_VERSION == 1
    assert FAILURE_SCHEMA_VERSION == 1


def test_core_result_objects_have_no_github_fields() -> None:
    assert {item.name for item in fields(SuiteRunResult)} == {
        "results",
        "duration_seconds",
        "runtime",
    }
    assert {item.name for item in fields(TaskRunResult)} == {
        "task_id",
        "agent_result",
        "verifier_result",
        "duration_seconds",
        "workspace_changes",
    }
    assert {item.name for item in fields(AgentRunResult)} == {
        "agent",
        "exit_code",
        "events",
        "stdout",
        "stderr",
        "duration_seconds",
        "requested_model",
        "resolved_model",
        "failure",
    }
    assert {item.name for item in fields(VerifierResult)} == {
        "command",
        "exit_code",
        "stdout",
        "stderr",
        "duration_seconds",
    }
    assert {item.name for item in fields(MatrixRunResult)} == {
        "targets",
        "duration_seconds",
        "runtime_backend",
    }
    assert {item.name for item in fields(MatrixTargetResult)} == {
        "target_id",
        "agent_type",
        "requested_model",
        "suite_result",
        "error_type",
        "error_message",
        "duration_seconds",
    }


def test_core_modules_do_not_import_github_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    relative_paths = (
        "agentdocs/agents",
        "agentdocs/verifier",
        "agentdocs/workspace_changes.py",
        "agentdocs/fingerprint.py",
        "agentdocs/execution",
        "agentdocs/runner.py",
        "agentdocs/suite.py",
        "agentdocs/matrix.py",
        "agentdocs/artifacts.py",
        "agentdocs/matrix_artifacts.py",
    )
    for relative in relative_paths:
        path = root / relative
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file in files:
            text = file.read_text(encoding="utf-8")
            assert "github_actions" not in text
            assert "GITHUB_" not in text


def test_github_module_uses_file_interfaces_only() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "agentdocs" / "github_actions.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "api.github.com",
        "graphql",
        "urllib",
        "httpx",
        "requests",
        "::error::",
        "::warning::",
        "::notice::",
        "::set-output",
        "subprocess",
    ):
        assert forbidden not in text


def github_files_fresh(directory: Path, name: str) -> tuple[Path, Path]:
    folder = directory / name
    folder.mkdir()
    summary = folder / "summary.md"
    output = folder / "output.txt"
    summary.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    return summary, output


def _suite_outputs() -> dict[str, str]:
    return {
        "status": "pass",
        "exit_code": "0",
        "run_id": "run-1",
        "artifact_path": "/tmp/runs/run-1",
        "benchmark_sha256": _SHA,
        "suite_passed": "true",
        "execution_complete": "true",
        "runtime_backend": "local",
    }


def _suite(
    *,
    passed: bool,
    failure: AgentFailureClassification | None = None,
    model: str | None = None,
    changes: int = 0,
    runtime: RuntimeInfo | None = None,
    task_id: str = "create_user",
) -> SuiteRunResult:
    manifest = empty_workspace_changes()
    if changes:
        manifest = replace(
            manifest,
            added_files=tuple(
                AddedFile(path=f"file-{index}.txt", sha256="b" * 64, size_bytes=1)
                for index in range(changes)
            ),
        )
    result = SuiteRunResult(
        results=(
            TaskRunResult(
                task_id=task_id,
                agent_result=AgentRunResult(
                    agent="cursor",
                    exit_code=0 if failure is None else 1,
                    events=(),
                    stdout=_STDOUT,
                    stderr=_STDERR,
                    duration_seconds=1.0,
                    requested_model=model,
                    failure=failure,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0 if passed else 1,
                    stdout=_VERIFIER_STDOUT,
                    stderr=_VERIFIER_STDERR,
                    duration_seconds=0.1,
                ),
                duration_seconds=1.2,
                workspace_changes=manifest,
            ),
        ),
        duration_seconds=1.2,
    )
    if runtime is not None:
        result = replace(result, runtime=runtime)
    return result


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


def _matrix(
    statuses: tuple[str, ...],
    *,
    backend: str = "local",
    image: str | None = None,
) -> MatrixRunResult:
    targets: list[MatrixTargetResult] = []
    for index, status in enumerate(statuses):
        target_id = ("alpha", "beta", "gamma")[index]
        if status == "error":
            targets.append(
                MatrixTargetResult(
                    target_id=target_id,
                    agent_type="cursor",
                    requested_model=None,
                    suite_result=None,
                    error_type="DockerDaemonUnavailableError",
                    error_message="SECRET_INFRA /opt/secret",
                    duration_seconds=0.2,
                )
            )
            continue
        runtime = None
        if image is not None:
            runtime = RuntimeInfo(
                schema_version=RUNTIME_SCHEMA_VERSION,
                backend="docker",
                agent=DockerImageInfo(image, "sha256:hidden", "bridge"),
                verifier=DockerImageInfo("verifier:local", "sha256:hidden", "none"),
            )
        targets.append(
            MatrixTargetResult(
                target_id=target_id,
                agent_type="cursor",
                requested_model=None if index == 0 else "model-a",
                suite_result=_suite(
                    passed=status == "pass",
                    failure=_quota() if status == "provider_error" else None,
                    runtime=runtime,
                ),
                error_type=None,
                error_message=None,
                duration_seconds=2.0,
            )
        )
    return MatrixRunResult(targets=tuple(targets), duration_seconds=4.0, runtime_backend=backend)


def _assert_private_text_absent(markdown: str) -> None:
    for secret in (
        _PROMPT,
        _STDOUT,
        _STDERR,
        _VERIFIER_STDOUT,
        _VERIFIER_STDERR,
        _ENV_SECRET,
        _HOST_PATH,
    ):
        assert secret not in markdown


def _unescaped_pipes(line: str) -> int:
    count = 0
    escaped = False
    for character in line:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "|":
            count += 1
    return count
