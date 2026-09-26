"""Check that a task verifier path is an existing directory."""

from agentdocs.config.task_config import TaskConfig


def _require_verifier_directory(task: TaskConfig) -> None:
    path = task.verify.path
    if not path.exists():
        raise FileNotFoundError(
            f"Verifier directory for task {task.id!r} does not exist: {path}"
        )
    if not path.is_dir():
        raise NotADirectoryError(
            f"Verifier path for task {task.id!r} is not a directory: {path}"
        )
