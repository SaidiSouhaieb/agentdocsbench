from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentdocs.cli import app
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig
from agentdocs.fingerprint import compute_benchmark_fingerprint, fingerprint_manifest_document, fingerprint_summary

runner = CliRunner()


def _document(
    directory: Path,
    *,
    run_id: str,
    task_ids: list[tuple[str, bool]],
    requested_model: str | None = "model-a",
    schema_version: int = 2,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "run_id": run_id,
        "agent_type": "cursor",
        "requested_model": requested_model,
        "summary": {
            "total": len(task_ids),
            "passed": sum(passed for _task_id, passed in task_ids),
            "failed": sum(not passed for _task_id, passed in task_ids),
            "suite_passed": all(passed for _task_id, passed in task_ids),
            "duration_seconds": 4.5,
        },
        "tasks": [
            {
                "id": task_id,
                "passed": passed,
                "duration_seconds": 1.5,
                "agent": {"exit_code": 0},
                "verifier": {"exit_code": 0 if passed else 1},
            }
            for task_id, passed in task_ids
        ],
    }
    if schema_version == 1:
        del payload["requested_model"]
    path = directory / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_compare_two_runs_exits_zero_when_a_task_failed(tmp_path: Path) -> None:
    first = _document(
        tmp_path / "a",
        run_id="run-a",
        task_ids=[("create_user", True), ("create_project", False)],
        requested_model="model-a",
    )
    second = _document(
        tmp_path / "b",
        run_id="run-b",
        task_ids=[("create_user", True), ("create_project", True)],
        requested_model=None,
    )

    result = runner.invoke(app, ["compare", str(first), str(second.parent)])

    assert result.exit_code == 0
    assert "Benchmark identity: UNVERIFIED" in result.stdout
    assert "predate benchmark fingerprinting" in result.stdout
    assert "cannot be independently proven" not in result.stdout
    assert "FAIL" in result.stdout
    assert "PASS" in result.stdout
    assert "provider default" in result.stdout
    assert "model-a" in result.stdout
    assert "winner" not in result.stdout.lower()
    assert "A: 1/2" in result.stdout
    assert "B: 2/2" in result.stdout


def test_compare_three_runs(tmp_path: Path) -> None:
    paths = [
        _document(tmp_path / name, run_id=name, task_ids=[("create_user", True)])
        for name in ("a", "b", "c")
    ]

    result = runner.invoke(app, ["compare", *(str(path) for path in paths)])

    assert result.exit_code == 0
    assert "C = cursor" in result.stdout


def test_compare_warns_when_task_sets_differ(tmp_path: Path) -> None:
    first = _document(tmp_path / "a", run_id="run-a", task_ids=[("create_user", True)])
    second = _document(tmp_path / "b", run_id="run-b", task_ids=[("pagination", False)])

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Warning: task sets differ" in result.stdout
    assert "—" in result.stdout or "-" in result.stdout


def test_compare_schema_v1_says_model_was_not_recorded(tmp_path: Path) -> None:
    first = _document(
        tmp_path / "a",
        run_id="run-a",
        task_ids=[("create_user", True)],
        schema_version=1,
    )
    second = _document(
        tmp_path / "b",
        run_id="run-b",
        task_ids=[("create_user", True)],
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "not recorded" in result.stdout
    assert "Benchmark identity: UNVERIFIED" in result.stdout


def test_missing_run_exits_two(tmp_path: Path) -> None:
    present = _document(tmp_path / "a", run_id="run-a", task_ids=[("create_user", True)])

    result = runner.invoke(app, ["compare", str(present), str(tmp_path / "missing.json")])

    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_invalid_artifact_exits_two(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    present = _document(tmp_path / "a", run_id="run-a", task_ids=[("create_user", True)])

    result = runner.invoke(app, ["compare", str(present), str(broken)])

    assert result.exit_code == 2
    assert "not valid JSON" in result.stderr


def _tree(root: Path) -> AgentDocsConfig:
    docs = root / "docs"
    starter = root / "starter"
    user = root / "verifiers" / "create_user"
    project = root / "verifiers" / "create_project"
    for path in (docs, starter, user, project):
        path.mkdir(parents=True, exist_ok=True)
    (docs / "users.md").write_bytes(b"users\n")
    (starter / "main.py").write_bytes(b"print(1)\n")
    (user / "check.py").write_bytes(b"raise SystemExit(0)\n")
    (project / "check.py").write_bytes(b"raise SystemExit(0)\n")
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="cursor", model="model-a"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create Alice.",
                verify=VerifyConfig(path=user, command="python3 check.py"),
            ),
            TaskConfig(
                id="create_project",
                prompt="Create the project.",
                verify=VerifyConfig(path=project, command="python3 check.py"),
            ),
        ],
    )


def _save_v3(
    directory: Path,
    config: AgentDocsConfig,
    *,
    run_id: str,
    agent: str,
    model: str | None,
    passed: bool,
    write_manifest: bool = True,
    manifest_text: str | None = None,
    schema_version: int = 3,
) -> Path:
    fingerprint = compute_benchmark_fingerprint(config)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "run_id": run_id,
        "agent_type": agent,
        "requested_model": model,
        "benchmark": fingerprint_summary(fingerprint),
        "summary": {
            "total": 1,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
            "suite_passed": passed,
            "duration_seconds": 2.0,
        },
        "tasks": [
            {
                "id": "create_user",
                "passed": passed,
                "duration_seconds": 1.0,
                "agent": {"exit_code": 0, "failure": None} if schema_version >= 5 else {"exit_code": 0},
                "verifier": {"exit_code": 0 if passed else 1},
            }
        ],
    }
    if schema_version >= 5:
        payload["execution"] = {
            "complete": True,
            "blocking_agent_failures": 0,
            "unknown_agent_failures": 0,
        }
    path = directory / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if manifest_text is not None:
        (directory / "benchmark.json").write_text(manifest_text, encoding="utf-8")
    elif write_manifest:
        (directory / "benchmark.json").write_text(
            json.dumps(fingerprint_manifest_document(fingerprint)),
            encoding="utf-8",
        )
    return path


def test_compare_v4_and_v3_with_the_same_fingerprint_match(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    first = _save_v3(
        tmp_path / "a",
        benchmark,
        run_id="run-a",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=4,
    )
    second = _save_v3(
        tmp_path / "b",
        benchmark,
        run_id="run-b",
        agent="codex",
        model="model-b",
        passed=False,
        schema_version=3,
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in result.stdout
    assert "FAIL" in result.stdout


def test_compare_v4_runs_with_the_same_fingerprint_match(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    first = _save_v3(
        tmp_path / "a",
        benchmark,
        run_id="run-a",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=4,
    )
    second = _save_v3(
        tmp_path / "b",
        benchmark,
        run_id="run-b",
        agent="cursor",
        model="model-b",
        passed=True,
        schema_version=4,
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in result.stdout


def test_compare_v5_runs_show_execution_health(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    first = _save_v3(
        tmp_path / "a",
        benchmark,
        run_id="run-a",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=5,
    )
    second = _save_v3(
        tmp_path / "b",
        benchmark,
        run_id="run-b",
        agent="claude",
        model=None,
        passed=False,
        schema_version=5,
    )
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["execution"] = {
        "complete": False,
        "blocking_agent_failures": 1,
        "unknown_agent_failures": 0,
    }
    payload["tasks"][0]["agent"]["failure"] = {
        "schema_version": 1,
        "kind": "quota_or_credit",
        "blocking": True,
        "rule_id": "claude.credit_balance_low",
        "source": "stderr",
    }
    second.write_text(json.dumps(payload), encoding="utf-8")
    (second.parent / "agent.stderr.log").write_text(
        "Credit balance is too low\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in result.stdout
    assert "A execution: complete" in result.stdout
    assert "B execution: incomplete · quota_or_credit ×1" in result.stdout
    assert "Credit balance is too low" not in result.stdout


def test_compare_v4_and_v5_match_and_v2_is_unverified(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    current = _save_v3(
        tmp_path / "new",
        benchmark,
        run_id="run-new",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=5,
    )
    older = _save_v3(
        tmp_path / "old",
        benchmark,
        run_id="run-old",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=4,
    )
    legacy = _document(tmp_path / "legacy", run_id="run-legacy", task_ids=[("create_user", True)])

    matched = runner.invoke(app, ["compare", str(older), str(current)])
    unverified = runner.invoke(app, ["compare", str(legacy), str(current)])

    assert matched.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in matched.stdout
    assert "execution: not recorded" in matched.stdout
    assert "A execution: complete" in matched.stdout or "B execution: complete" in matched.stdout
    assert unverified.exit_code == 0
    assert "Benchmark identity: UNVERIFIED" in unverified.stdout
    assert "execution: not recorded" in unverified.stdout


def test_compare_v2_and_v4_are_unverified(tmp_path: Path) -> None:
    current = _save_v3(
        tmp_path / "new",
        _tree(tmp_path / "benchmark"),
        run_id="run-new",
        agent="cursor",
        model="model-a",
        passed=True,
        schema_version=4,
    )
    old = _document(tmp_path / "old", run_id="run-old", task_ids=[("create_user", True)])

    result = runner.invoke(app, ["compare", str(old), str(current)])

    assert result.exit_code == 0
    assert "Benchmark identity: UNVERIFIED" in result.stdout


def test_compare_v3_runs_with_the_same_fingerprint_are_verified(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    codex = benchmark.model_copy(update={"agent": AgentConfig(type="codex", model="model-b")})
    first = _save_v3(tmp_path / "a", benchmark, run_id="run-a", agent="cursor", model="model-a", passed=True)
    second = _save_v3(tmp_path / "b", codex, run_id="run-b", agent="codex", model="model-b", passed=False)

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in result.stdout
    assert "Fingerprint: sha256:" in result.stdout
    assert "UNVERIFIED" not in result.stdout
    assert "DIFFERENT" not in result.stdout
    assert "FAIL" in result.stdout
    assert "codex" in result.stdout
    assert "cursor" in result.stdout


def test_compare_v3_runs_show_a_docs_change(tmp_path: Path) -> None:
    left = _tree(tmp_path / "left")
    right = _tree(tmp_path / "right")
    (right.docs / "users.md").write_bytes(b"changed\n")
    first = _save_v3(tmp_path / "a", left, run_id="run-a", agent="cursor", model="model-a", passed=True)
    second = _save_v3(tmp_path / "b", right, run_id="run-b", agent="cursor", model="model-a", passed=True)

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: DIFFERENT" in result.stdout
    assert "modified:" in result.stdout
    assert "users.md" in result.stdout
    assert "PASS" in result.stdout


def test_compare_v3_runs_show_a_prompt_change_without_the_prompt(tmp_path: Path) -> None:
    left = _tree(tmp_path / "benchmark")
    tasks = list(left.tasks)
    tasks[0] = tasks[0].model_copy(update={"prompt": "Create Bob instead."})
    right = left.model_copy(update={"tasks": tasks})
    first = _save_v3(tmp_path / "a", left, run_id="run-a", agent="cursor", model=None, passed=True)
    second = _save_v3(tmp_path / "b", right, run_id="run-b", agent="cursor", model=None, passed=True)

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: DIFFERENT" in result.stdout
    assert "create_user:" in result.stdout
    assert "prompt changed" in result.stdout
    assert "Create Bob instead." not in result.stdout
    assert "Create Alice." not in result.stdout


def test_compare_v3_runs_show_a_verifier_change(tmp_path: Path) -> None:
    left = _tree(tmp_path / "left")
    right = _tree(tmp_path / "right")
    (right.tasks[0].verify.path / "check.py").write_bytes(b"raise SystemExit(1)\n")
    first = _save_v3(tmp_path / "a", left, run_id="run-a", agent="cursor", model="model-a", passed=True)
    second = _save_v3(tmp_path / "b", right, run_id="run-b", agent="cursor", model="model-a", passed=True)

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "check.py" in result.stdout
    assert "modified:" in result.stdout


def test_compare_v1_with_v3_is_unverified(tmp_path: Path) -> None:
    current = _save_v3(
        tmp_path / "new",
        _tree(tmp_path / "benchmark"),
        run_id="run-new",
        agent="cursor",
        model="model-a",
        passed=True,
    )
    old = _document(
        tmp_path / "old",
        run_id="run-old",
        task_ids=[("create_user", True)],
        schema_version=1,
    )

    result = runner.invoke(app, ["compare", str(old), str(current)])

    assert result.exit_code == 0
    assert "Benchmark identity: UNVERIFIED" in result.stdout
    assert "B: sha256:" in result.stdout
    assert "A: not recorded" in result.stdout


def test_compare_three_matching_v3_runs(tmp_path: Path) -> None:
    benchmark = _tree(tmp_path / "benchmark")
    paths = [
        _save_v3(tmp_path / name, benchmark, run_id=name, agent="cursor", model="model-a", passed=True)
        for name in ("a", "b", "c")
    ]

    result = runner.invoke(app, ["compare", *(str(path) for path in paths)])

    assert result.exit_code == 0
    assert "Benchmark identity: VERIFIED MATCH" in result.stdout
    assert result.stdout.count("Fingerprint: sha256:") == 1


def test_compare_three_v3_runs_groups_distinct_fingerprints(tmp_path: Path) -> None:
    first = _tree(tmp_path / "one")
    second = _tree(tmp_path / "two")
    (second.docs / "users.md").write_bytes(b"other\n")
    paths = [
        _save_v3(tmp_path / "a", first, run_id="a", agent="cursor", model="model-a", passed=True),
        _save_v3(tmp_path / "b", second, run_id="b", agent="cursor", model="model-a", passed=True),
        _save_v3(tmp_path / "c", first, run_id="c", agent="codex", model="model-b", passed=False),
    ]

    result = runner.invoke(app, ["compare", *(str(path) for path in paths)])

    assert result.exit_code == 0
    assert "Benchmark identity: DIFFERENT" in result.stdout
    assert "Detailed change manifest is available when comparing two runs." in result.stdout
    assert "users.md" not in result.stdout
    assert "A, C" in result.stdout


def test_missing_benchmark_manifest_still_reports_identity(tmp_path: Path) -> None:
    left = _tree(tmp_path / "left")
    right = _tree(tmp_path / "right")
    (right.docs / "users.md").write_bytes(b"other\n")
    first = _save_v3(
        tmp_path / "a",
        left,
        run_id="run-a",
        agent="cursor",
        model="model-a",
        passed=False,
        write_manifest=False,
    )
    second = _save_v3(
        tmp_path / "b",
        right,
        run_id="run-b",
        agent="cursor",
        model="model-a",
        passed=True,
        write_manifest=False,
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: DIFFERENT" in result.stdout
    assert "Detailed benchmark manifest unavailable for run A." in result.stdout
    assert "FAIL" in result.stdout


def test_malformed_benchmark_manifest_does_not_hide_the_result_table(tmp_path: Path) -> None:
    left = _tree(tmp_path / "left")
    right = _tree(tmp_path / "right")
    (right.docs / "users.md").write_bytes(b"other\n")
    first = _save_v3(tmp_path / "a", left, run_id="run-a", agent="cursor", model="model-a", passed=True)
    second = _save_v3(
        tmp_path / "b",
        right,
        run_id="run-b",
        agent="cursor",
        model="model-a",
        passed=False,
        manifest_text="{",
    )

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == 0
    assert "Benchmark identity: DIFFERENT" in result.stdout
    assert "Detailed benchmark manifest unavailable for run B." in result.stdout
    assert "not valid JSON" in result.stdout
    assert "FAIL" in result.stdout
