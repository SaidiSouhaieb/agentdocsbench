"""Matrix runtime selection. Docker calls are mocked."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentdocs.agents.base import AgentAdapter, AgentRunResult
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig
from agentdocs.execution.docker import DockerExecutionRuntime
from agentdocs.fingerprint import compute_benchmark_fingerprint
from agentdocs.matrix import run_matrix, target_status
from agentdocs.matrix_artifacts import write_matrix_artifacts
from agentdocs.matrix_config import MatrixConfig, MatrixTargetConfig
from agentdocs.runtime_config import load_runtime_config
from tests.docker_script import DockerScript


class _FakeAgent(AgentAdapter):
    def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
        del prompt, docs_dir, project_dir
        return AgentRunResult(
            agent="fake",
            exit_code=0,
            events=(),
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )


def _benchmark(tmp_path: Path) -> AgentDocsConfig:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    verifier = tmp_path / "verifier"
    starter.mkdir()
    docs.mkdir()
    verifier.mkdir()
    (starter / "app.py").write_text("print('starter')\n", encoding="utf-8")
    (docs / "users.md").write_text("docs\n", encoding="utf-8")
    (verifier / "check.py").write_text("print('ok')\n", encoding="utf-8")
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="cursor"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create a user.",
                verify=VerifyConfig(path=verifier, command="python3 check.py"),
            )
        ],
    )


def _matrix(*targets: tuple[str, str]) -> MatrixConfig:
    return MatrixConfig(
        version=1,
        targets=[
            MatrixTargetConfig(id=target_id, agent=agent) for target_id, agent in targets
        ],
    )


def _runtime(tmp_path: Path, text: str, script: DockerScript, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")

    def which(name: str) -> str | None:
        return "/usr/bin/docker" if name == "docker" else None

    monkeypatch.setattr("agentdocs.execution.docker.shutil.which", which)
    monkeypatch.setattr("agentdocs.execution.docker.subprocess.run", script)
    return DockerExecutionRuntime(load_runtime_config(path))


_BOTH = """\
version: 1
backend: docker
agents:
  cursor:
    image: agentdocs-cursor:local
    network: bridge
  claude:
    image: agentdocs-claude:local
    network: none
verifier:
  image: agentdocs-verifier:local
"""


def test_targets_select_their_own_images_and_keep_one_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    runtime = _runtime(tmp_path, _BOTH, script, monkeypatch)
    config = _benchmark(tmp_path)
    fingerprint = compute_benchmark_fingerprint(config)

    result = run_matrix(
        config,
        _matrix(("cursor-default", "cursor"), ("claude-default", "claude")),
        agent_factory=_FakeAgent,
        runtime=runtime,
    )

    assert [target_status(target) for target in result.targets] == ["pass", "pass"]
    assert result.runtime_backend == "docker"
    cursor = result.targets[0].suite_result
    claude = result.targets[1].suite_result
    assert cursor is not None and claude is not None
    assert cursor.runtime.agent is not None and claude.runtime.agent is not None
    assert cursor.runtime.agent.requested_image == "agentdocs-cursor:local"
    assert cursor.runtime.agent.image_id == "sha256:ccc333"
    assert claude.runtime.agent.requested_image == "agentdocs-claude:local"
    assert claude.runtime.agent.image_id == "sha256:bbb222"
    assert cursor.runtime.verifier is not None
    assert cursor.runtime.verifier.network == "none"
    assert cursor.runtime.agent.network == "bridge"
    assert claude.runtime.agent.network == "none"
    verifier_inspects = [
        args
        for args in script.calls
        if args[:4] == ["docker", "image", "inspect", "agentdocs-verifier:local"]
    ]
    assert len(verifier_inspects) == 1
    artifacts = write_matrix_artifacts(
        result,
        task_ids=("create_user",),
        run_ids={"cursor-default": "run-cursor", "claude-default": "run-claude"},
        output_root=tmp_path / "matrices",
        benchmark_fingerprint=fingerprint,
    )
    import json

    payload = json.loads(artifacts.matrix_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 4
    assert payload["targets"][0]["runtime"]["agent"]["requested_image"] == "agentdocs-cursor:local"
    assert payload["targets"][1]["runtime"]["agent"]["image_id"] == "sha256:bbb222"
    assert payload["benchmark"]["overall_sha256"] == fingerprint.overall_sha256
    benchmark = json.loads(artifacts.benchmark_json.read_text(encoding="utf-8"))
    assert benchmark["schema_version"] == 1
    from agentdocs.artifacts import write_run_artifacts
    from agentdocs.execution.runtime_info import local_runtime_info
    from agentdocs.suite import SuiteRunResult

    docker_run = write_run_artifacts(
        config,
        cursor,
        output_root=tmp_path / "docker-runs",
        benchmark_fingerprint=fingerprint,
    )
    local_run = write_run_artifacts(
        config,
        SuiteRunResult(
            results=cursor.results,
            duration_seconds=cursor.duration_seconds,
            runtime=local_runtime_info(),
        ),
        output_root=tmp_path / "local-runs",
        benchmark_fingerprint=fingerprint,
    )
    docker_result = json.loads(docker_run.result_json.read_text(encoding="utf-8"))
    local_result = json.loads(local_run.result_json.read_text(encoding="utf-8"))
    assert docker_result["schema_version"] == 6
    assert local_result["schema_version"] == 6
    assert docker_result["runtime"]["backend"] == "docker"
    assert local_result["runtime"]["backend"] == "local"
    assert docker_result["benchmark"]["overall_sha256"] == local_result["benchmark"]["overall_sha256"]
    assert "runtime.yaml" not in docker_run.result_json.read_text(encoding="utf-8")


def test_missing_agent_image_is_error_and_the_later_target_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    runtime = _runtime(
        tmp_path,
        """\
version: 1
backend: docker
agents:
  claude:
    image: agentdocs-claude:local
verifier:
  image: agentdocs-verifier:local
""",
        script,
        monkeypatch,
    )

    result = run_matrix(
        _benchmark(tmp_path),
        _matrix(("cursor-default", "cursor"), ("claude-default", "claude")),
        agent_factory=_FakeAgent,
        runtime=runtime,
    )

    assert target_status(result.targets[0]) == "error"
    assert result.targets[0].error_type == "RuntimeConfigError"
    assert target_status(result.targets[1]) == "pass"
    assert result.targets[0].suite_result is None
    assert result.targets[1].suite_result is not None


def test_missing_docker_image_is_error_and_the_later_target_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = DockerScript()
    script.images.pop("agentdocs-cursor:local")
    runtime = _runtime(tmp_path, _BOTH, script, monkeypatch)

    result = run_matrix(
        _benchmark(tmp_path),
        _matrix(("cursor-default", "cursor"), ("claude-default", "claude")),
        agent_factory=_FakeAgent,
        runtime=runtime,
    )

    assert target_status(result.targets[0]) == "error"
    assert result.targets[0].error_type == "DockerImageNotFoundError"
    assert target_status(result.targets[1]) == "pass"


def test_provider_error_inside_docker_stays_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentdocs.agents.failures import (
        FAILURE_SCHEMA_VERSION,
        AgentFailureClassification,
        AgentFailureKind,
    )

    class _QuotaAgent(AgentAdapter):
        def run(self, prompt: str, project_dir: Path, docs_dir: Path) -> AgentRunResult:
            del prompt, project_dir, docs_dir
            return AgentRunResult(
                agent="codex",
                exit_code=1,
                events=(),
                stdout="",
                stderr="",
                duration_seconds=0.01,
                failure=AgentFailureClassification(
                    schema_version=FAILURE_SCHEMA_VERSION,
                    kind=AgentFailureKind.quota_or_credit,
                    blocking=True,
                    rule_id="codex.usage_limit",
                    source="structured_event",
                ),
            )

    script = DockerScript()
    runtime = _runtime(tmp_path, _BOTH, script, monkeypatch)
    produced = {"count": 0}

    def factory() -> AgentAdapter:
        produced["count"] += 1
        if produced["count"] == 1:
            return _QuotaAgent()
        return _FakeAgent()

    result = run_matrix(
        _benchmark(tmp_path),
        _matrix(("cursor-quota", "cursor"), ("claude-default", "claude")),
        agent_factory=factory,
        runtime=runtime,
    )

    assert target_status(result.targets[0]) == "provider_error"
    assert target_status(result.targets[1]) == "pass"
    assert result.targets[0].suite_result is not None

