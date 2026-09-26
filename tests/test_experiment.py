from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureClassification,
    AgentFailureKind,
)
from agentdocs.config.with_docs import with_docs
from agentdocs.execution.docker import DOCS_MOUNT, agent_create_args
from agentdocs.execution.local import LocalExecutionRuntime
from agentdocs.experiment import (
    ExperimentInvariantError,
    aggregate_transition_counts,
    experiment_comparability,
    experiment_exit_code,
    experiment_status,
    run_docs_experiment,
)
from agentdocs.experiment_config import DocsExperimentConfig, load_experiment_config
from agentdocs.fingerprint import BenchmarkFingerprint, compute_benchmark_fingerprint
from tests.experiment_support import load_pair, suite_for


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


def test_variants_run_in_file_order_when_reference_is_not_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)
    experiment = DocsExperimentConfig(
        version=1,
        reference="reference",
        variants=[experiment.variants[1], experiment.variants[0]],
    )
    seen: list[Path] = []

    def fake_run_suite(copied, **_kwargs):
        seen.append(copied.docs)
        passed = copied.docs.name == "candidate"
        return suite_for((("docs_task", passed, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    original_docs = config.docs

    result = run_docs_experiment(config, experiment)

    assert seen == [experiment.variants[0].docs, experiment.variants[1].docs]
    assert [item.variant_id for item in result.variants] == ["candidate", "reference"]
    assert [item.position for item in result.variants] == [0, 1]
    assert result.comparisons[0].variant_id == "candidate"
    assert result.comparisons[0].reference_variant == "reference"
    assert config.docs == original_docs


def test_all_five_transitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(
        tmp_path,
        ("stay_pass", "stay_fail", "gained", "lost", "blocked"),
    )
    reference = (
        ("stay_pass", True, None),
        ("stay_fail", False, None),
        ("gained", False, None),
        ("lost", True, None),
        ("blocked", True, None),
    )
    candidate = (
        ("stay_pass", True, None),
        ("stay_fail", False, None),
        ("gained", True, None),
        ("lost", False, None),
        ("blocked", False, _quota()),
    )

    def fake_run_suite(copied, **_kwargs):
        outcomes = reference if copied.docs.name == "reference" else candidate
        return suite_for(outcomes)

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    transitions = {task.task_id: task.transition for task in result.comparisons[0].tasks}

    assert transitions == {
        "stay_pass": "unchanged_pass",
        "stay_fail": "unchanged_fail",
        "gained": "newly_passing",
        "lost": "newly_failing",
        "blocked": "not_comparable",
    }
    blocked = result.comparisons[0].tasks[-1]
    assert blocked.reference_result == "PASS"
    assert blocked.variant_result == "FAIL"
    assert experiment_status(result) == "provider_error"
    assert experiment_exit_code(result) == 2


def test_unknown_nonblocking_failure_can_be_newly_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        if copied.docs.name == "reference":
            return suite_for((("docs_task", False, None),))
        return suite_for((("docs_task", True, _unknown()),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    assert result.comparisons[0].tasks[0].transition == "newly_passing"
    assert experiment_exit_code(result) == 0


def test_infrastructure_error_is_not_comparable_and_later_variant_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdocs.agents.base import AgentError

    config, experiment = load_pair(tmp_path)
    calls: list[str] = []

    def fake_run_suite(copied, **_kwargs):
        calls.append(copied.docs.name)
        if copied.docs.name == "reference":
            raise AgentError("SECRET_INFRA /opt/host")
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    assert calls == ["reference", "candidate"]
    assert result.variants[0].status == "error"
    assert result.variants[0].suite_result is None
    assert result.variants[0].error_type == "AgentError"
    assert result.comparisons[0].tasks[0].transition == "not_comparable"
    assert result.variants[1].status == "pass"
    assert experiment_status(result) == "error"
    assert experiment_exit_code(result) == 2


def test_multiple_candidates_compare_only_with_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)
    third = tmp_path / "docs" / "third"
    experiment = load_experiment_config(_three_variant_file(tmp_path, experiment, third))

    def fake_run_suite(copied, **_kwargs):
        return suite_for((("docs_task", copied.docs.name != "reference", None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    assert [item.variant_id for item in result.comparisons] == ["candidate", "third"]
    assert {item.reference_variant for item in result.comparisons} == {"reference"}
    pairs = {(item.reference_variant, item.variant_id) for item in result.comparisons}
    assert ("candidate", "third") not in pairs
    assert ("third", "candidate") not in pairs


def test_completed_verifier_failures_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        passed = copied.docs.name == "candidate"
        return suite_for((("docs_task", passed, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    assert result.variants[0].status == "fail"
    assert result.variants[1].status == "pass"
    assert result.comparisons[0].tasks[0].transition == "newly_passing"
    assert experiment_status(result) == "complete"
    assert experiment_exit_code(result) == 0
    assert aggregate_transition_counts(result)["newly_failing"] == 0


def test_unexpected_exception_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(_copied, **_kwargs):
        raise RuntimeError("programming bug")

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)

    with pytest.raises(RuntimeError, match="programming bug"):
        run_docs_experiment(config, experiment)


def test_invariant_mismatch_stops_before_the_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)
    real = compute_benchmark_fingerprint
    calls: list[str] = []

    def fake_fingerprint(copied):
        fingerprint = real(copied)
        if copied.docs.name == "candidate":
            return BenchmarkFingerprint(
                schema_version=fingerprint.schema_version,
                algorithm=fingerprint.algorithm,
                overall_sha256="c" * 64,
                docs=fingerprint.docs,
                starter=fingerprint.starter,
                tasks_sha256="b" * 64,
                tasks=fingerprint.tasks,
                verifiers_sha256=fingerprint.verifiers_sha256,
                verifiers=fingerprint.verifiers,
            )
        return fingerprint

    def fake_run_suite(_copied, **_kwargs):
        calls.append("ran")
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.compute_benchmark_fingerprint", fake_fingerprint)
    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    reordered = DocsExperimentConfig(
        version=1,
        reference="reference",
        variants=[experiment.variants[1], experiment.variants[0]],
    )

    with pytest.raises(ExperimentInvariantError, match="tasks"):
        run_docs_experiment(config, reordered)

    assert calls == []


def test_same_runtime_object_and_variant_docs_are_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)
    runtime = LocalExecutionRuntime()
    seen: list[tuple[Path, object]] = []

    def fake_run_suite(copied, **kwargs):
        seen.append((copied.docs, kwargs["runtime"]))
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    run_docs_experiment(config, experiment, runtime=runtime)

    assert seen[0][1] is runtime
    assert seen[1][1] is runtime
    assert seen[0][0] == experiment.variants[0].docs
    assert seen[1][0] == experiment.variants[1].docs


def test_docker_mount_uses_the_docs_tree_read_only(tmp_path: Path) -> None:
    reference = tmp_path / "reference"
    candidate = tmp_path / "candidate"
    project = tmp_path / "project"
    for path in (reference, candidate, project):
        path.mkdir()
    reference_args = agent_create_args(
        name="agentdocs-agent-abc",
        image="agentdocs-docs-experiment-smoke:local",
        command=["agent"],
        host_project=project,
        host_docs=reference,
        network="bridge",
        extra_mounts=[],
        environment={},
    )
    candidate_args = agent_create_args(
        name="agentdocs-agent-def",
        image="agentdocs-docs-experiment-smoke:local",
        command=["agent"],
        host_project=project,
        host_docs=candidate,
        network="bridge",
        extra_mounts=[],
        environment={},
    )

    reference_text = " ".join(reference_args)
    candidate_text = " ".join(candidate_args)
    assert f"source={reference},target={DOCS_MOUNT},readonly" in reference_text
    assert f"source={candidate},target={DOCS_MOUNT},readonly" in candidate_text
    assert "/workspace/verifier" not in reference_text
    assert "/workspace/verifier" not in candidate_text


def test_workspace_copies_the_variant_docs(tmp_path: Path) -> None:
    from agentdocs.workspace import create_workspace

    config, experiment = load_pair(tmp_path)
    copied = with_docs(config, experiment.variants[1].docs)

    with create_workspace(copied) as workspace:
        text = (workspace.docs / "a.md").read_text(encoding="utf-8")

    assert text == "candidate docs\n"
    assert config.docs != copied.docs


def test_docs_delta_is_content_free_and_ordered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(tmp_path)
    reference = experiment.variants[0].docs
    candidate = experiment.variants[1].docs
    (reference / "b.md").write_text("keep\n", encoding="utf-8")
    (reference / "old.md").write_text("gone\n", encoding="utf-8")
    (reference / ".hidden").write_text("secret-bytes\n", encoding="utf-8")
    (reference / "shape").write_text("file\n", encoding="utf-8")
    (reference / "blank").mkdir()
    (candidate / "a.md").write_bytes(b"\x00\x01changed")
    (candidate / "b.md").write_text("keep\n", encoding="utf-8")
    (candidate / ".hidden").write_text("other-bytes\n", encoding="utf-8")
    (candidate / "c.md").write_text("added\n", encoding="utf-8")
    (candidate / "blank").mkdir()
    (candidate / "shape").mkdir()
    (candidate / "shape" / "inside.txt").write_text("dir\n", encoding="utf-8")
    (candidate / "empty-only").mkdir()

    def fake_run_suite(_copied, **_kwargs):
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    changes = result.comparisons[0].docs_changes
    assert changes is not None
    assert changes.modified_files == (".hidden", "a.md")
    assert "old.md" in changes.removed_files
    assert "c.md" in changes.added_files
    assert "empty-only" in changes.added_directories
    assert any(item.path == "shape" and item.before == "file" and item.after == "directory" for item in changes.type_changes)
    rendered = " ".join(changes.added_files + changes.modified_files)
    assert "secret-bytes" not in rendered
    assert "\x00" not in rendered


def test_resolved_model_mismatch_warns_without_hiding_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(copied, **_kwargs):
        model = "model-a" if copied.docs.name == "reference" else "model-b"
        passed = copied.docs.name == "candidate"
        return suite_for((("docs_task", passed, None),), resolved_model=model)

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    info = experiment_comparability(result)

    assert info.resolved_model_differs
    assert info.resolved_models == ("model-a", "model-b")
    assert result.comparisons[0].tasks[0].transition == "newly_passing"


def test_null_resolved_models_are_not_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, experiment = load_pair(tmp_path)

    def fake_run_suite(_copied, **_kwargs):
        return suite_for((("docs_task", True, None),))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)
    info = experiment_comparability(result)

    assert info.resolved_models == ()
    assert not info.resolved_model_differs


def test_different_docker_image_ids_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentdocs.execution.runtime_info import RUNTIME_SCHEMA_VERSION, DockerImageInfo, RuntimeInfo
    from dataclasses import replace

    config, experiment = load_pair(tmp_path)

    def runtime_for(name: str) -> RuntimeInfo:
        return RuntimeInfo(
            schema_version=RUNTIME_SCHEMA_VERSION,
            backend="docker",
            agent=DockerImageInfo("image:local", f"sha256:{name}", "bridge"),
            verifier=DockerImageInfo("image:local", "sha256:verifier", "none"),
        )

    def fake_run_suite(copied, **_kwargs):
        suite = suite_for((("docs_task", True, None),))
        image = "aaaa" if copied.docs.name == "reference" else "bbbb"
        return replace(suite, runtime=runtime_for(image))

    monkeypatch.setattr("agentdocs.experiment.run_suite", fake_run_suite)
    result = run_docs_experiment(config, experiment)

    assert experiment_comparability(result).runtime_image_ids_differ
    assert result.comparisons[0].tasks[0].transition == "unchanged_pass"


def _three_variant_file(root: Path, experiment: DocsExperimentConfig, third: Path) -> Path:
    del experiment, third
    path = root / "three.yaml"
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
    return path
