from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from agentdocs import (
    AgentDocsConfig,
    AgentRunResult,
    ArtifactError,
    SuiteRunResult,
    TaskRunResult,
    VerifierResult,
    write_run_artifacts,
)
from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.config import AgentConfig, TaskConfig, VerifyConfig
from agentdocs.fingerprint import (
    BenchmarkFingerprint,
    TreeManifest,
    compute_benchmark_fingerprint,
)

_UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _config() -> AgentDocsConfig:
    return AgentDocsConfig.model_construct(
        version=1,
        docs=Path("/docs"),
        starter=Path("/starter"),
        agent=AgentConfig.model_construct(type="codex"),
        tasks=[],
    )


def _task(
    task_id: str,
    *,
    agent_exit: int = 0,
    verifier_exit: int = 0,
    agent_stdout: str = "agent-out",
    agent_stderr: str = "agent-err",
    verifier_stdout: str = "verifier-out",
    verifier_stderr: str = "verifier-err",
    events: tuple[dict[str, object], ...] = ({"type": "item"},),
    duration: float = 22.5831,
    agent_duration: float = 22.1,
    verifier_duration: float = 0.05,
) -> TaskRunResult:
    return TaskRunResult(
        task_id=task_id,
        agent_result=AgentRunResult(
            agent="codex",
            exit_code=agent_exit,
            events=events,
            stdout=agent_stdout,
            stderr=agent_stderr,
            duration_seconds=agent_duration,
        ),
        verifier_result=VerifierResult(
            command=("python3", "check.py"),
            exit_code=verifier_exit,
            stdout=verifier_stdout,
            stderr=verifier_stderr,
            duration_seconds=verifier_duration,
        ),
        duration_seconds=duration,
    )


def _suite(*tasks: TaskRunResult, duration: float = 46.7) -> SuiteRunResult:
    return SuiteRunResult(results=tasks, duration_seconds=duration)


def _stub_fingerprint() -> BenchmarkFingerprint:
    empty = TreeManifest(directories=(), files=(), sha256="a" * 64)
    return BenchmarkFingerprint(
        schema_version=1,
        algorithm="sha256",
        overall_sha256="b" * 64,
        docs=empty,
        starter=empty,
        tasks_sha256="c" * 64,
        tasks=(),
        verifiers_sha256="d" * 64,
        verifiers=(),
    )


def _persist(config: AgentDocsConfig, result: SuiteRunResult, *, output_root: Path):
    return write_run_artifacts(
        config,
        result,
        output_root=output_root,
        benchmark_fingerprint=_stub_fingerprint(),
    )


def test_writes_logs_in_result_order(tmp_path: Path) -> None:
    result = _suite(
        _task("create_user", agent_stdout="", agent_stderr="", verifier_stdout="", verifier_stderr=""),
        _task("create_project", agent_stdout="project-out\n", agent_stderr="project-err"),
    )

    artifacts = _persist(_config(), result, output_root=tmp_path)

    assert artifacts.root.is_dir()
    assert artifacts.root.parent == tmp_path
    assert artifacts.result_json.is_file()
    assert artifacts.summary_markdown.is_file()
    folders = sorted(path.name for path in (artifacts.root / "tasks").iterdir())
    assert folders == ["001-create_user", "002-create_project"]
    first = artifacts.root / "tasks" / "001-create_user"
    second = artifacts.root / "tasks" / "002-create_project"
    assert (first / "agent.stdout.log").read_text(encoding="utf-8") == ""
    assert (first / "agent.stderr.log").read_text(encoding="utf-8") == ""
    assert (first / "verifier.stdout.log").read_text(encoding="utf-8") == ""
    assert (first / "verifier.stderr.log").read_text(encoding="utf-8") == ""
    assert (second / "agent.stdout.log").read_text(encoding="utf-8") == "project-out\n"
    assert (second / "agent.stderr.log").read_text(encoding="utf-8") == "project-err"
    assert (second / "verifier.stdout.log").read_text(encoding="utf-8") == "verifier-out"
    assert (second / "verifier.stderr.log").read_text(encoding="utf-8") == "verifier-err"


def test_result_json_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDOCS_SHOULD_NOT_LEAK", "leak-marker-xyz")
    result = _suite(
        _task("create_user", events=({"a": 1}, {"b": 2})),
        _task("create_project", verifier_exit=1),
        duration=46.7,
    )

    artifacts = _persist(_config(), result, output_root=tmp_path)
    text = artifacts.result_json.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert text.endswith("\n")
    assert payload["schema_version"] == 6
    assert payload["runtime"] == {"schema_version": 1, "backend": "local"}
    assert payload["benchmark"]["overall_sha256"] == "b" * 64
    changes = payload["tasks"][0]["changes"]
    assert changes["files_added"] == 0
    assert changes["manifest_file"] == "tasks/001-create_user/changes.json"
    assert (artifacts.root / changes["manifest_file"]).is_file()
    changes_text = (artifacts.root / changes["manifest_file"]).read_text(encoding="utf-8")
    assert "leak-marker-xyz" not in changes_text
    assert "/private/var" not in changes_text
    assert payload["benchmark"]["manifest_path"] == "benchmark.json"
    assert artifacts.benchmark_json.is_file()
    assert payload["run_id"] == artifacts.run_id
    assert _UTC_STAMP.match(payload["created_at_utc"])
    assert payload["agent_type"] == "codex"
    assert payload["requested_model"] is None
    assert payload["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "suite_passed": False,
        "duration_seconds": 46.7,
    }
    assert [task["id"] for task in payload["tasks"]] == ["create_user", "create_project"]
    first = payload["tasks"][0]
    assert first["agent"]["requested_model"] is None
    assert first["agent"]["resolved_model"] is None
    assert first["passed"] is True
    assert first["duration_seconds"] == 22.5831
    assert first["agent"]["exit_code"] == 0
    assert first["agent"]["completed_cleanly"] is True
    assert first["agent"]["failure"] is None
    assert payload["execution"] == {
        "complete": True,
        "blocking_agent_failures": 0,
        "unknown_agent_failures": 0,
    }
    assert first["agent"]["duration_seconds"] == 22.1
    assert first["agent"]["event_count"] == 2
    assert first["verifier"]["command"] == ["python3", "check.py"]
    assert first["verifier"]["exit_code"] == 0
    assert first["verifier"]["passed"] is True
    assert first["verifier"]["duration_seconds"] == 0.05
    for key in ("stdout_file", "stderr_file"):
        relative = first["agent"][key]
        assert not Path(relative).is_absolute()
        assert (artifacts.root / relative).is_file()
    for key in ("stdout_file", "stderr_file"):
        relative = first["verifier"][key]
        assert relative.startswith("tasks/001-create_user/")
        assert (artifacts.root / relative).is_file()
    assert "/tmp/agentdocs" not in text
    assert "/private/var" not in text
    assert "leak-marker-xyz" not in text
    assert "events" not in text


def test_summary_markdown_includes_pass_and_fail(tmp_path: Path) -> None:
    artifacts = _persist(
        _config(),
        _suite(
            _task("create_user"),
            _task("create_project", verifier_exit=1, duration=24.1),
            duration=46.7,
        ),
        output_root=tmp_path,
    )
    summary = artifacts.summary_markdown.read_text(encoding="utf-8")

    assert f"Run ID: `{artifacts.run_id}`" in summary
    assert "Requested model: provider default" in summary
    assert "Resolved model: not reported" in summary
    assert "| create_user | PASS |" in summary
    assert "| create_project | FAIL |" in summary
    assert "- Total: 2" in summary
    assert "- Passed: 1" in summary
    assert "- Failed: 1" in summary
    assert "- Duration: 46.70s" in summary
    assert "Benchmark fingerprint: `sha256:" + ("b" * 64) + "`" in summary
    assert "Fingerprint schema: 1" in summary
    assert "\x1b" not in summary


def test_requested_model_is_stored_without_entering_the_run_id(tmp_path: Path) -> None:
    config = _config().model_copy(
        update={"agent": AgentConfig.model_construct(type="cursor", model="model/a")}
    )
    task = _task("create_user")
    task = TaskRunResult(
        task_id=task.task_id,
        agent_result=AgentRunResult(
            agent="cursor",
            exit_code=0,
            events=(),
            stdout="",
            stderr="",
            duration_seconds=1.0,
            requested_model="model/a",
            resolved_model=None,
        ),
        verifier_result=task.verifier_result,
        duration_seconds=task.duration_seconds,
    )

    artifacts = _persist(config, _suite(task), output_root=tmp_path)
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    summary = artifacts.summary_markdown.read_text(encoding="utf-8")

    assert payload["requested_model"] == "model/a"
    assert payload["tasks"][0]["agent"]["requested_model"] == "model/a"
    assert payload["tasks"][0]["agent"]["resolved_model"] is None
    assert "model/a" not in artifacts.run_id
    assert "Requested model: `model/a`" in summary
    assert "API_KEY" not in summary


def test_failed_suite_is_still_written(tmp_path: Path) -> None:
    artifacts = _persist(
        _config(),
        _suite(_task("create_user"), _task("create_project", verifier_exit=1)),
        output_root=tmp_path,
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))

    assert payload["summary"]["suite_passed"] is False
    assert payload["tasks"][1]["passed"] is False
    assert (artifacts.root / "tasks" / "002-create_project" / "verifier.stderr.log").is_file()


def test_pass_state_follows_verifier(tmp_path: Path) -> None:
    artifacts = _persist(
        _config(),
        _suite(
            _task("agent_failed", agent_exit=1, verifier_exit=0),
            _task("verifier_failed", agent_exit=0, verifier_exit=1),
        ),
        output_root=tmp_path,
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))

    assert payload["tasks"][0]["passed"] is True
    assert payload["tasks"][0]["agent"]["exit_code"] == 1
    assert payload["tasks"][0]["agent"]["completed_cleanly"] is False
    assert payload["tasks"][1]["passed"] is False
    assert payload["tasks"][1]["agent"]["completed_cleanly"] is True


def test_task_ids_stay_inside_the_tasks_directory(tmp_path: Path) -> None:
    artifacts = _persist(
        _config(),
        _suite(_task("../../outside"), _task("create/user")),
        output_root=tmp_path,
    )
    tasks_root = artifacts.root / "tasks"
    folders = sorted(tasks_root.iterdir())

    assert len(folders) == 2
    for folder in folders:
        assert folder.resolve().is_relative_to(tasks_root.resolve())
        assert "/" not in folder.name
        assert "\\" not in folder.name
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path.parent / "outside").exists()
    assert folders[1].name.startswith("002-")


def test_pipe_in_task_id_does_not_break_the_markdown_table(tmp_path: Path) -> None:
    artifacts = _persist(
        _config(),
        _suite(_task("a|b")),
        output_root=tmp_path,
    )
    summary = artifacts.summary_markdown.read_text(encoding="utf-8")
    row = next(line for line in summary.splitlines() if "PASS" in line)

    assert row == "| a\\|b | PASS | 0 | 0 | +0 ~0 -0 | 22.58s |"


def test_two_writes_do_not_replace_an_existing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _persist(_config(), _suite(_task("create_user")), output_root=tmp_path)
    second = _persist(_config(), _suite(_task("create_user")), output_root=tmp_path)

    assert first.root != second.root
    assert first.root.is_dir()
    assert second.root.is_dir()

    class _FixedUUID:
        hex = "12345678123456781234567812345678"

    monkeypatch.setattr(uuid, "uuid4", lambda: _FixedUUID())
    kept = (first.root / "result.json").read_text(encoding="utf-8")
    _persist(_config(), _suite(_task("create_user")), output_root=tmp_path)
    occupied = tmp_path / first.root.name
    with pytest.raises(ArtifactError, match="already exists|unique run id"):
        monkeypatch.setattr(
            "agentdocs.artifacts._new_run_id",
            lambda: occupied.name,
        )
        _persist(_config(), _suite(_task("other")), output_root=tmp_path)

    assert (first.root / "result.json").read_text(encoding="utf-8") == kept
    assert "other" not in (first.root / "result.json").read_text(encoding="utf-8")


def test_output_root_is_created_when_missing(tmp_path: Path) -> None:
    output_root = tmp_path / "missing" / "runs"

    artifacts = _persist(
        _config(),
        _suite(_task("create_user")),
        output_root=output_root,
    )

    assert output_root.is_dir()
    assert artifacts.root.is_relative_to(output_root)


@pytest.mark.parametrize("agent_type", ["claude", "cursor"])
def test_result_json_records_agent_harness(tmp_path: Path, agent_type: str) -> None:
    config = AgentDocsConfig.model_construct(
        version=1,
        docs=Path("/docs"),
        starter=Path("/starter"),
        agent=AgentConfig.model_construct(type=agent_type),
        tasks=[],
    )
    task = _task("create_user")
    task = TaskRunResult(
        task_id=task.task_id,
        agent_result=AgentRunResult(
            agent=agent_type,
            exit_code=0,
            events=(),
            stdout="",
            stderr="",
            duration_seconds=1.0,
        ),
        verifier_result=task.verifier_result,
        duration_seconds=task.duration_seconds,
    )
    artifacts = _persist(config, _suite(task), output_root=tmp_path)
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    assert payload["agent_type"] == agent_type
    assert payload["tasks"][0]["agent"]["name"] == agent_type


def test_output_root_file_raises(tmp_path: Path) -> None:
    output_root = tmp_path / "not-a-directory"
    output_root.write_text("x", encoding="utf-8")

    with pytest.raises(ArtifactError, match="not a directory"):
        _persist(_config(), _suite(_task("create_user")), output_root=output_root)


def test_write_failure_leaves_no_completed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_path: Path, _text: str) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("agentdocs.artifacts._write_text", fail_write)

    with pytest.raises(ArtifactError, match="permission denied"):
        _persist(_config(), _suite(_task("create_user")), output_root=tmp_path)

    assert list(tmp_path.glob(".run-*.tmp")) == []
    assert [path for path in tmp_path.iterdir() if not path.name.startswith(".")] == []


def _benchmark_config(root: Path, *, agent: str = "cursor", model: str | None = None) -> AgentDocsConfig:
    docs = root / "docs"
    starter = root / "starter"
    verifier = root / "verifiers" / "create_user"
    docs.mkdir(parents=True, exist_ok=True)
    starter.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(parents=True, exist_ok=True)
    (docs / "users.md").write_bytes(b"Alice uses the users API.\n")
    (starter / "main.py").write_bytes(b"print('start')\n")
    (verifier / "check.py").write_bytes(b"raise SystemExit(0)\n")
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type=agent, model=model),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create a user named Alice.",
                verify=VerifyConfig(path=verifier, command="python3 check.py"),
            )
        ],
    )


def test_schema_v3_stores_a_relative_benchmark_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTDOCS_SHOULD_NOT_LEAK", "leak-marker-xyz")
    root = tmp_path / "benchmark"
    config = _benchmark_config(root)
    fingerprint = compute_benchmark_fingerprint(config)

    artifacts = write_run_artifacts(
        config,
        _suite(_task("create_user")),
        output_root=tmp_path / "runs",
        benchmark_fingerprint=fingerprint,
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    manifest_text = artifacts.benchmark_json.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    summary = artifacts.summary_markdown.read_text(encoding="utf-8")

    assert payload["schema_version"] == 6
    assert payload["runtime"] == {"schema_version": 1, "backend": "local"}
    assert payload["benchmark"]["fingerprint_schema_version"] == 1
    assert payload["benchmark"]["algorithm"] == "sha256"
    assert payload["benchmark"]["overall_sha256"] == fingerprint.overall_sha256
    assert payload["benchmark"]["docs_sha256"] == fingerprint.docs_sha256
    assert payload["benchmark"]["starter_sha256"] == fingerprint.starter_sha256
    assert payload["benchmark"]["tasks_sha256"] == fingerprint.tasks_sha256
    assert payload["benchmark"]["verifiers_sha256"] == fingerprint.verifiers_sha256
    assert payload["benchmark"]["manifest_path"] == "benchmark.json"
    assert manifest["schema_version"] == 1
    assert manifest["components"]["docs"]["files"] == [
        {
            "path": "users.md",
            "sha256": fingerprint.docs.files[0].sha256,
            "size_bytes": fingerprint.docs.files[0].size_bytes,
        }
    ]
    assert manifest["components"]["verifiers"]["items"][0]["task_id"] == "create_user"
    assert "Create a user named Alice." not in manifest_text
    assert "Alice uses the users API." not in manifest_text
    assert str(root.resolve()) not in manifest_text
    assert "leak-marker-xyz" not in manifest_text
    assert f"Benchmark fingerprint: `sha256:{fingerprint.overall_sha256}`" in summary
    changes = payload["tasks"][0]["changes"]
    changes_path = artifacts.root / changes["manifest_file"]
    changes_text = changes_path.read_text(encoding="utf-8")
    changes_payload = json.loads(changes_text)
    assert changes["manifest_file"] == "tasks/001-create_user/changes.json"
    assert changes["files_added"] == 0
    assert changes["files_modified"] == 0
    assert changes["files_deleted"] == 0
    assert changes["before_sha256"] == changes["after_sha256"]
    assert changes_payload["schema_version"] == 1
    assert changes_payload["algorithm"] == "sha256"
    assert changes_payload["summary"]["files_added"] == 0
    assert changes_payload["before_sha256"] == changes["before_sha256"]
    assert "leak-marker-xyz" not in changes_text
    assert str(root.resolve()) not in changes_text
    assert "+ added, ~ modified, - deleted" in summary


def test_explicit_fingerprint_is_written_without_rehashing(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    config = _benchmark_config(root)
    selected = compute_benchmark_fingerprint(config)
    (root / "docs" / "users.md").write_bytes(b"changed after selection\n")

    artifacts = write_run_artifacts(
        config,
        _suite(_task("create_user")),
        output_root=tmp_path / "runs",
        benchmark_fingerprint=selected,
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))

    assert payload["benchmark"]["overall_sha256"] == selected.overall_sha256
    assert payload["benchmark"]["overall_sha256"] != compute_benchmark_fingerprint(config).overall_sha256


def test_omitted_fingerprint_is_computed_from_the_config(tmp_path: Path) -> None:
    config = _benchmark_config(tmp_path / "benchmark")

    artifacts = write_run_artifacts(
        config,
        _suite(_task("create_user")),
        output_root=tmp_path / "runs",
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))

    assert payload["benchmark"]["overall_sha256"] == compute_benchmark_fingerprint(config).overall_sha256


def test_schema_v5_records_execution_health_without_matched_text(tmp_path: Path) -> None:
    quota = AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.quota_or_credit,
        blocking=True,
        rule_id="claude.credit_balance_low",
        source="stderr",
    )
    unknown = AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=AgentFailureKind.unknown_agent_failure,
        blocking=False,
        rule_id="unmatched_nonzero_exit",
        source="exit_code",
    )
    artifacts = write_run_artifacts(
        _config(),
        _suite(
            TaskRunResult(
                task_id="create_user",
                agent_result=AgentRunResult(
                    agent="claude",
                    exit_code=1,
                    events=(),
                    stdout="",
                    stderr="Credit balance is too low\n",
                    duration_seconds=1.0,
                    failure=quota,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=1,
                    stdout="",
                    stderr="",
                    duration_seconds=0.1,
                ),
                duration_seconds=1.1,
            ),
            TaskRunResult(
                task_id="create_project",
                agent_result=AgentRunResult(
                    agent="claude",
                    exit_code=1,
                    events=(),
                    stdout="",
                    stderr="boom\n",
                    duration_seconds=1.0,
                    failure=unknown,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0,
                    stdout="",
                    stderr="",
                    duration_seconds=0.1,
                ),
                duration_seconds=1.1,
            ),
        ),
        output_root=tmp_path / "classified",
        benchmark_fingerprint=_stub_fingerprint(),
    )
    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    changes = json.loads(
        (artifacts.root / "tasks/001-create_user/changes.json").read_text(encoding="utf-8")
    )
    benchmark = json.loads(artifacts.benchmark_json.read_text(encoding="utf-8"))
    text = artifacts.result_json.read_text(encoding="utf-8")

    assert payload["schema_version"] == 6
    assert payload["runtime"] == {"schema_version": 1, "backend": "local"}
    assert benchmark["schema_version"] == 1
    assert changes["schema_version"] == 1
    assert payload["execution"]["complete"] is False
    assert payload["execution"]["blocking_agent_failures"] == 1
    assert payload["execution"]["unknown_agent_failures"] == 1
    assert payload["tasks"][0]["agent"]["failure"]["rule_id"] == "claude.credit_balance_low"
    assert payload["tasks"][1]["agent"]["failure"]["kind"] == "unknown_agent_failure"
    assert payload["tasks"][1]["agent"]["failure"]["blocking"] is False
    assert "Credit balance is too low" not in text
    assert "Credit balance is too low" in (
        artifacts.root / "tasks/001-create_user/agent.stderr.log"
    ).read_text(encoding="utf-8")
    assert "Status: INCOMPLETE" in artifacts.summary_markdown.read_text(encoding="utf-8")
