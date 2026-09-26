from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentdocs.agents.failures import FAILURE_SCHEMA_VERSION
from agentdocs.artifacts import _SCHEMA_VERSION as RESULT_SCHEMA_VERSION
from agentdocs.artifacts import write_run_artifacts
from agentdocs.config.with_docs import with_docs
from agentdocs.execution.runtime_info import RUNTIME_SCHEMA_VERSION
from agentdocs.experiment import experiment_exit_code, run_docs_experiment
from agentdocs.experiment_artifacts import EXPERIMENT_SCHEMA_VERSION, write_experiment_artifacts
from agentdocs.fingerprint import FINGERPRINT_SCHEMA_VERSION
from agentdocs.matrix_artifacts import _SCHEMA_VERSION as MATRIX_SCHEMA_VERSION
from agentdocs.workspace_changes import CHANGES_SCHEMA_VERSION
from tests.experiment_support import PROMPT, load_pair, suite_for

_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def test_experiment_artifact_is_schema_1_and_content_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)
    monkeypatch.setenv("PROVIDER_TOKEN", "ENV_SECRET_SHOULD_NOT_APPEAR")

    def fake_run_suite(copied, **_kwargs):
        passed = copied.docs.name == "candidate"
        return suite_for((("docs_task", passed, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    run_ids = {}
    runs = tmp_path / "runs"
    for variant, spec in zip(result.variants, experiment.variants, strict=True):
        assert variant.fingerprint is not None and variant.suite_result is not None
        written = write_run_artifacts(
            with_docs(config, spec.docs),
            variant.suite_result,
            output_root=runs,
            benchmark_fingerprint=variant.fingerprint,
        )
        run_ids[variant.variant_id] = written.run_id
        payload = json.loads((written.root / "result.json").read_text(encoding="utf-8"))
        assert payload["schema_version"] == 6
        assert payload["runtime"]["schema_version"] == 1
        benchmark = json.loads((written.root / "benchmark.json").read_text(encoding="utf-8"))
        assert benchmark["schema_version"] == 1
        changes = json.loads(next((written.root / "tasks").rglob("changes.json")).read_text(encoding="utf-8"))
        assert changes["schema_version"] == 1

    parent = write_experiment_artifacts(result, run_ids=run_ids, output_root=tmp_path / "experiments")
    document = json.loads(parent.experiment_json.read_text(encoding="utf-8"))
    summary = parent.summary_markdown.read_text(encoding="utf-8")
    raw = parent.experiment_json.read_text(encoding="utf-8")

    assert document["schema_version"] == 1
    assert _RUN_ID.fullmatch(document["experiment_id"])
    assert document["reference_variant"] == "reference"
    assert document["controlled_components"]["starter_sha256"] == result.controlled_starter_sha256
    assert document["variants"][0]["docs_sha256"] != document["variants"][1]["docs_sha256"]
    assert document["variants"][0]["benchmark_sha256"] != document["variants"][1]["benchmark_sha256"]
    assert document["controlled_components"]["tasks_sha256"] == result.controlled_tasks_sha256
    assert document["controlled_components"]["verifiers_sha256"] == result.controlled_verifiers_sha256
    assert document["variants"][0]["run_id"] == run_ids["reference"]
    assert document["variants"][1]["run_id"] == run_ids["candidate"]
    assert document["comparisons"][0]["tasks"][0]["transition"] == "newly_passing"
    assert document["comparisons"][0]["docs_changes"]["files_modified"] == ["a.md"]
    assert document["transition_counts"]["newly_passing"] == 1
    assert "newly passing: 1" in summary
    assert "causal effect" in summary
    for secret in (
        PROMPT,
        "AGENT_STDOUT_SHOULD_NOT_APPEAR",
        "AGENT_STDERR_SHOULD_NOT_APPEAR",
        "VERIFIER_STDOUT_SHOULD_NOT_APPEAR",
        "VERIFIER_STDERR_SHOULD_NOT_APPEAR",
        "ENV_SECRET_SHOULD_NOT_APPEAR",
        str(experiment.variants[0].docs),
        str(experiment.variants[1].docs),
        "reference docs",
        "candidate docs",
    ):
        assert secret not in raw
        assert secret not in summary
    assert experiment_exit_code(result) == 0


def test_provider_error_variant_keeps_a_child_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentdocs.agents.failures import AgentFailureKind

    config, experiment = load_pair(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        from tests.experiment_support import suite_for as build
        from agentdocs.agents.failures import AgentFailureClassification

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
        return build((("docs_task", passed, failure),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    written = write_run_artifacts(
        with_docs(config, experiment.variants[1].docs),
        result.variants[1].suite_result,  # type: ignore[arg-type]
        output_root=tmp_path / "runs",
        benchmark_fingerprint=result.variants[1].fingerprint,  # type: ignore[arg-type]
    )
    parent = write_experiment_artifacts(
        result,
        run_ids={"reference": "ref-run", "candidate": written.run_id},
        output_root=tmp_path / "experiments",
    )
    document = json.loads(parent.experiment_json.read_text(encoding="utf-8"))
    candidate = document["variants"][1]
    child = json.loads((written.root / "result.json").read_text(encoding="utf-8"))

    assert candidate["status"] == "provider_error"
    assert candidate["run_id"] == written.run_id
    assert child["schema_version"] == 6
    assert child["tasks"][0]["agent"]["failure"]["schema_version"] == 1
    assert (written.root / "tasks").exists()
    assert document["comparisons"][0]["tasks"][0]["transition"] == "not_comparable"


def test_error_variant_has_no_run_id_and_later_child_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdocs.agents.base import AgentError

    config, experiment = load_pair(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        if copied.docs.name == "reference":
            raise AgentError("boom")
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    written = write_run_artifacts(
        with_docs(config, experiment.variants[1].docs),
        result.variants[1].suite_result,  # type: ignore[arg-type]
        output_root=tmp_path / "runs",
        benchmark_fingerprint=result.variants[1].fingerprint,  # type: ignore[arg-type]
    )
    parent = write_experiment_artifacts(
        result,
        run_ids={"candidate": written.run_id},
        output_root=tmp_path / "experiments",
    )
    document = json.loads(parent.experiment_json.read_text(encoding="utf-8"))

    assert "run_id" not in document["variants"][0]
    assert document["variants"][0]["error_type"] == "AgentError"
    assert document["variants"][0]["status"] == "error"
    assert document["variants"][1]["run_id"] == written.run_id
    assert written.root.is_dir()
    assert document["comparisons"][0]["tasks"][0]["transition"] == "not_comparable"


def test_parent_write_failure_removes_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(_copied, **_kwargs):
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    def fail_rename(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("agentdocs.experiment_artifacts.os.rename", fail_rename)
    output = tmp_path / "experiments"

    with pytest.raises(Exception, match="Could not write experiment artifacts"):
        write_experiment_artifacts(result, run_ids={}, output_root=output)

    assert list(output.glob("*")) == []
    assert list(output.glob(".*")) == []


def test_persisted_schema_versions_stay_in_place() -> None:
    assert RESULT_SCHEMA_VERSION == 6
    assert MATRIX_SCHEMA_VERSION == 4
    assert FINGERPRINT_SCHEMA_VERSION == 1
    assert CHANGES_SCHEMA_VERSION == 1
    assert RUNTIME_SCHEMA_VERSION == 1
    assert FAILURE_SCHEMA_VERSION == 1
    assert EXPERIMENT_SCHEMA_VERSION == 1
