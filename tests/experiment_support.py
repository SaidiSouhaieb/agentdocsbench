"""Shared fixtures for documentation-experiment tests."""

from __future__ import annotations

from pathlib import Path

from agentdocs.agents.base import AgentRunResult
from agentdocs.agents.failures import AgentFailureClassification
from agentdocs.config import AgentDocsConfig, load_config
from agentdocs.experiment_config import DocsExperimentConfig, load_experiment_config
from agentdocs.runner import TaskRunResult
from agentdocs.suite import SuiteRunResult
from agentdocs.verifier.result import VerifierResult
from agentdocs.workspace_changes import empty_workspace_changes

PROMPT = "PROMPT_SHOULD_NOT_APPEAR"


def write_benchmark(root: Path, tasks: tuple[str, ...] = ("docs_task",)) -> tuple[Path, Path]:
    """Create a two-variant benchmark. Docs bytes differ. Other inputs match."""
    (root / "docs" / "reference").mkdir(parents=True)
    (root / "docs" / "candidate").mkdir(parents=True)
    (root / "docs" / "third").mkdir(parents=True)
    (root / "starter").mkdir()
    (root / "verifier").mkdir()
    (root / "docs" / "reference" / "a.md").write_text("reference docs\n", encoding="utf-8")
    (root / "docs" / "candidate" / "a.md").write_text("candidate docs\n", encoding="utf-8")
    (root / "docs" / "third" / "a.md").write_text("third docs\n", encoding="utf-8")
    (root / "starter" / "README.md").write_text("starter\n", encoding="utf-8")
    (root / "verifier" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    config = root / "agentdocs.yaml"
    task_yaml = "\n".join(
        f"""\
  - id: {task_id}
    prompt: {PROMPT}
    verify:
      path: ./verifier
      command: python3 check.py"""
        for task_id in tasks
    )
    config.write_text(
        f"""\
version: 1
docs: ./docs/reference
starter: ./starter
agent:
  type: cursor
tasks:
{task_yaml}
""",
        encoding="utf-8",
    )
    experiment = root / "docs-experiment.yaml"
    experiment.write_text(
        """\
version: 1
reference: reference
variants:
  - id: reference
    docs: ./docs/reference
  - id: candidate
    docs: ./docs/candidate
""",
        encoding="utf-8",
    )
    return config, experiment


def load_pair(
    root: Path, tasks: tuple[str, ...] = ("docs_task",)
) -> tuple[AgentDocsConfig, DocsExperimentConfig]:
    config_path, experiment_path = write_benchmark(root, tasks)
    return load_config(config_path), load_experiment_config(experiment_path)


def suite_for(
    outcomes: tuple[tuple[str, bool, AgentFailureClassification | None], ...],
    *,
    resolved_model: str | None = None,
) -> SuiteRunResult:
    tasks = []
    for task_id, passed, failure in outcomes:
        tasks.append(
            TaskRunResult(
                task_id=task_id,
                agent_result=AgentRunResult(
                    agent="cursor",
                    exit_code=0 if failure is None else 1,
                    events=(),
                    stdout="AGENT_STDOUT_SHOULD_NOT_APPEAR\n",
                    stderr="AGENT_STDERR_SHOULD_NOT_APPEAR\n",
                    duration_seconds=0.1,
                    failure=failure,
                    resolved_model=resolved_model,
                ),
                verifier_result=VerifierResult(
                    command=("python3", "check.py"),
                    exit_code=0 if passed else 1,
                    stdout="VERIFIER_STDOUT_SHOULD_NOT_APPEAR\n",
                    stderr="VERIFIER_STDERR_SHOULD_NOT_APPEAR\n",
                    duration_seconds=0.1,
                ),
                duration_seconds=0.2,
                workspace_changes=empty_workspace_changes(),
            )
        )
    return SuiteRunResult(results=tuple(tasks), duration_seconds=0.2)
