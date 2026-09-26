from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentdocs.agents.base import AgentError
from agentdocs.agents.failures import FAILURE_SCHEMA_VERSION, AgentFailureClassification, AgentFailureKind
from agentdocs.cli import app
from tests.experiment_support import PROMPT, suite_for, write_benchmark
from tests.github_support import github_files, parse_github_output

runner = CliRunner()


def _patch(monkeypatch: pytest.MonkeyPatch, outcomes_for):
    calls: list[object] = []

    def fake_run_suite(config, **kwargs):
        calls.append(config)
        return outcomes_for(config)

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    return calls


def test_experiment_help_lists_the_experiment_file() -> None:
    result = runner.invoke(app, ["experiment", "--help"])

    assert result.exit_code == 0
    text = result.stdout
    assert "--experiment" in text
    assert "-e" in text
    assert "--github-actions" in text
    assert "--runtime-config" in text
    assert "--no-artifacts" in text


def test_completed_verifier_changes_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)

    def outcomes(copied):
        return suite_for((("docs_task", copied.docs.name == "candidate", None),))

    _patch(monkeypatch, outcomes)
    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(experiment), "--quiet"])

    assert result.exit_code == 0
    assert "newly passing: 1" in result.stdout
    assert "newly failing: 0" in result.stdout
    assert "Variant [" not in result.stdout
    assert "Experiment:" in result.stdout
    parent = _experiment_document(tmp_path)
    assert parent["schema_version"] == 1
    assert parent["variants"][0]["status"] == "fail"
    assert parent["variants"][1]["status"] == "pass"
    child = _child_result(tmp_path, parent["variants"][0]["run_id"])
    assert child["schema_version"] == 6


def test_newly_failing_still_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)

    def outcomes(copied):
        return suite_for((("docs_task", copied.docs.name == "reference", None),))

    _patch(monkeypatch, outcomes)
    result = runner.invoke(app, ["experiment", "--config", str(config), "--experiment", str(experiment)])

    assert result.exit_code == 0
    assert "newly failing: 1" in result.stdout
    assert "Variant [1/2] reference" in result.stdout
    assert "Variant [2/2] candidate" in result.stdout


def test_model_override_applies_to_every_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    calls = _patch(monkeypatch, lambda _copied: suite_for((("docs_task", True, None),)))

    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--model", "composer-2.5", "--quiet"],
    )

    assert result.exit_code == 0
    assert "Model: composer-2.5" in result.stdout
    assert [item.agent.model for item in calls] == ["composer-2.5", "composer-2.5"]


def test_runtime_config_is_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    runtime = tmp_path / "runtime.yaml"
    runtime.write_text(
        """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-docs-experiment-smoke:local
    network: bridge
    env_passthrough: []
    mounts: []
verifier:
  image: agentdocs-docs-experiment-smoke:local
""",
        encoding="utf-8",
    )
    seen: list[object] = []

    def fake_run_suite(copied, **kwargs):
        seen.append(kwargs["runtime"])
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = runner.invoke(
        app,
        [
            "experiment",
            "-c",
            str(config),
            "-e",
            str(experiment),
            "--runtime-config",
            str(runtime),
        ],
    )

    assert result.exit_code == 0
    assert "Runtime: docker" in result.stdout
    assert seen[0] is seen[1]
    assert seen[0].backend == "docker"


def test_verbose_shows_fingerprints_without_docs_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = write_benchmark(tmp_path)
    _patch(monkeypatch, lambda copied: suite_for((("docs_task", copied.docs.name == "candidate", None),)))

    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(experiment), "--verbose"])

    assert result.exit_code == 0
    assert "sha256:" in result.stdout
    assert "modified: a.md" in result.stdout
    assert "reference docs\n" not in result.stdout
    assert PROMPT not in result.stdout
    assert "Run reference:" in result.stdout


def test_debug_does_not_dump_prompts_or_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    monkeypatch.setenv("PROVIDER_TOKEN", "ENV_SECRET_SHOULD_NOT_APPEAR")
    _patch(monkeypatch, lambda _copied: suite_for((("docs_task", True, None),)))

    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(experiment), "--debug"])

    assert result.exit_code == 0
    combined = result.stdout + result.stderr
    assert PROMPT not in combined
    assert "ENV_SECRET_SHOULD_NOT_APPEAR" not in combined
    assert "reference docs" not in combined


def test_reference_not_first_keeps_file_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _experiment = write_benchmark(tmp_path)
    path = tmp_path / "docs-experiment.yaml"
    path.write_text(
        """\
version: 1
reference: reference
variants:
  - id: candidate
    docs: ./docs/candidate
  - id: reference
    docs: ./docs/reference
""",
        encoding="utf-8",
    )
    _patch(monkeypatch, lambda copied: suite_for((("docs_task", True, None),)))

    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(path), "--quiet"])

    assert result.exit_code == 0
    document = _experiment_document(tmp_path)
    assert [item["id"] for item in document["variants"]] == ["candidate", "reference"]
    assert document["comparisons"][0]["variant_id"] == "candidate"


def test_multiple_candidates_are_compared_only_with_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _experiment = write_benchmark(tmp_path)
    path = tmp_path / "docs-experiment.yaml"
    path.write_text(
        """\
version: 1
reference: reference
variants:
  - id: reference
    docs: ./docs/reference
  - id: candidate
    docs: ./docs/candidate
  - id: third
    docs: ./docs/third
""",
        encoding="utf-8",
    )
    _patch(monkeypatch, lambda _copied: suite_for((("docs_task", True, None),)))

    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(path), "--quiet"])

    assert result.exit_code == 0
    assert "candidate vs reference" in result.stdout
    assert "third vs reference" in result.stdout
    assert "candidate vs third" not in result.stdout
    assert "third vs candidate" not in result.stdout


def test_blocking_provider_failure_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)

    def outcomes(copied):
        failure = None
        passed = True
        if copied.docs.name == "candidate":
            failure = AgentFailureClassification(
                schema_version=FAILURE_SCHEMA_VERSION,
                kind=AgentFailureKind.quota_or_credit,
                blocking=True,
                rule_id="claude.credit_balance_low",
                source="stderr",
            )
            passed = False
        return suite_for((("docs_task", passed, failure),))

    _patch(monkeypatch, outcomes)
    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(experiment), "--quiet"])

    assert result.exit_code == 2
    assert "not comparable: 1" in result.stdout
    assert "newly failing: 0" in result.stdout


def test_infrastructure_error_exits_two_and_keeps_later_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = write_benchmark(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        if copied.docs.name == "reference":
            raise AgentError("boom")
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = runner.invoke(app, ["experiment", "-c", str(config), "-e", str(experiment), "--quiet"])

    assert result.exit_code == 2
    document = _experiment_document(tmp_path)
    assert document["status"] == "error"
    assert "run_id" not in document["variants"][0]
    assert document["variants"][1]["run_id"]
    child = tmp_path / ".agentdocs" / "runs" / document["variants"][1]["run_id"]
    assert (child / "result.json").is_file()


def test_no_artifacts_skips_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    _patch(monkeypatch, lambda _copied: suite_for((("docs_task", False, None),)))

    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--no-artifacts", "--quiet"],
    )

    assert result.exit_code == 0
    assert "unchanged fail: 1" in result.stdout
    assert "Run reference:" not in result.stdout
    assert not (tmp_path / ".agentdocs").exists()


def test_github_complete_does_not_fail_on_a_verifier_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _experiment = write_benchmark(tmp_path)
    experiment = tmp_path / "docs-experiment.yaml"
    experiment.write_text(
        """\
version: 1
reference: reference
variants:
  - id: reference
    docs: ./docs/reference
  - id: "cand<id>"
    docs: ./docs/candidate
""",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "candidate" / "notes.md").write_text("SECRET_DOC_BYTES\n", encoding="utf-8")
    summary, output = github_files(monkeypatch, tmp_path)
    monkeypatch.setenv("PROVIDER_TOKEN", "ENV_SECRET_SHOULD_NOT_APPEAR")
    _patch(monkeypatch, lambda copied: suite_for((("docs_task", copied.docs.name == "candidate", None),)))

    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--github-actions", "--quiet"],
    )

    assert result.exit_code == 0
    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    text = summary.read_text(encoding="utf-8")
    assert parsed["status"] == "complete"
    assert parsed["exit_code"] == "0"
    assert parsed["reference_variant"] == "reference"
    assert parsed["variant_count"] == "2"
    assert parsed["runtime_backend"] == "local"
    assert parsed["newly_passing"] == "1"
    assert parsed["newly_failing"] == "0"
    assert parsed["not_comparable"] == "0"
    assert parsed["experiment_run_id"]
    assert parsed["reference_benchmark_sha256"]
    assert "**Status:** COMPLETE" in text
    assert "cand&lt;id&gt;" in text
    assert "cand<id>" not in text
    assert PROMPT not in text
    assert "ENV_SECRET_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_DOC_BYTES" not in text
    assert "reference docs" not in text
    assert str(tmp_path / "docs" / "reference") not in text
    assert "AGENT_STDOUT_SHOULD_NOT_APPEAR" not in text


def test_github_newly_failing_stays_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    _summary, output = github_files(monkeypatch, tmp_path)
    _patch(monkeypatch, lambda copied: suite_for((("docs_task", copied.docs.name == "reference", None),)))

    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--github-actions", "--quiet"],
    )

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert parsed["status"] == "complete"
    assert parsed["newly_failing"] == "1"


def test_github_provider_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)

    def outcomes(copied):
        if copied.docs.name != "candidate":
            return suite_for((("docs_task", True, None),))
        failure = AgentFailureClassification(
            schema_version=FAILURE_SCHEMA_VERSION,
            kind=AgentFailureKind.quota_or_credit,
            blocking=True,
            rule_id="claude.credit_balance_low",
            source="stderr",
        )
        return suite_for((("docs_task", False, failure),))

    _patch(monkeypatch, outcomes)
    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--github-actions", "--quiet"],
    )

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert result.exit_code == 2
    assert parsed["status"] == "provider_error"
    assert parsed["not_comparable"] == "1"
    assert "**Status:** PROVIDER_ERROR" in summary.read_text(encoding="utf-8")


def test_github_infrastructure_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)

    def fake_run_suite(copied, **_kwargs):
        if copied.docs.name == "candidate":
            raise AgentError("SECRET_INFRA /tmp/should-not-be-in-summary")
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = runner.invoke(
        app,
        ["experiment", "-c", str(config), "-e", str(experiment), "--github-actions", "--quiet"],
    )

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    text = summary.read_text(encoding="utf-8")
    assert result.exit_code == 2
    assert parsed["status"] == "error"
    assert parsed["exit_code"] == "2"
    assert parsed["not_comparable"] == "1"
    assert "SECRET_INFRA" not in text
    assert "/tmp/should-not-be-in-summary" not in text


def test_github_no_artifacts_leaves_ids_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = write_benchmark(tmp_path)
    _summary, output = github_files(monkeypatch, tmp_path)
    _patch(monkeypatch, lambda copied: suite_for((("docs_task", copied.docs.name == "candidate", None),)))

    result = runner.invoke(
        app,
        [
            "experiment",
            "-c",
            str(config),
            "-e",
            str(experiment),
            "--github-actions",
            "--no-artifacts",
            "--quiet",
        ],
    )

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert parsed["status"] == "complete"
    assert parsed["experiment_run_id"] == ""
    assert parsed["artifact_path"] == ""
    assert parsed["newly_passing"] == "1"


def test_github_missing_experiment_file_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _experiment = write_benchmark(tmp_path)
    summary, output = github_files(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "experiment",
            "-c",
            str(config),
            "-e",
            str(tmp_path / "missing-experiment.yaml"),
            "--github-actions",
        ],
    )

    parsed = parse_github_output(output.read_text(encoding="utf-8"))
    text = summary.read_text(encoding="utf-8")
    assert result.exit_code == 2
    assert parsed["status"] == "error"
    assert parsed["experiment_run_id"] == ""
    assert "missing-experiment.yaml" not in text


def _experiment_document(root: Path) -> dict:
    matches = list((root / ".agentdocs" / "experiments").glob("*/experiment.json"))
    assert len(matches) == 1
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _child_result(root: Path, run_id: str) -> dict:
    path = root / ".agentdocs" / "runs" / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))
