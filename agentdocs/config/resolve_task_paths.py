"""Resolve verifier paths against the config file directory."""

from pathlib import Path

from agentdocs.config.resolve_against_config_dir import _resolve_against_config_dir
from agentdocs.config.task_config import TaskConfig


def _resolve_task_paths(base_dir: Path, tasks: list[TaskConfig]) -> list[TaskConfig]:
    resolved: list[TaskConfig] = []
    for task in tasks:
        verify = task.verify.model_copy(
            update={"path": _resolve_against_config_dir(base_dir, task.verify.path)}
        )
        resolved.append(task.model_copy(update={"verify": verify}))
    return resolved
