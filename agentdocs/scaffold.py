"""Create a minimal AgentDocsBench project.

The scaffold writes a valid benchmark the current loader accepts. It does not
run an agent, a verifier, or Docker.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from agentdocs.config.agent_config import AgentConfig

_TASK_ID = "first_task"
_EXPECTED = "HELLO_AGENTDOCS"


class InitProjectError(Exception):
    """The starter benchmark could not be created."""


def initialize_project(
    target_dir: str | Path,
    *,
    agent_type: str,
    model: str | None = None,
) -> Path:
    """Create a benchmark in ``target_dir``.

    A missing directory is created. An existing empty directory is filled in.
    A directory that already contains anything is refused, and nothing already
    there is overwritten. A failed write does not leave a partial scaffold.
    """
    _validate_agent(agent_type, model)
    target = Path(target_dir)
    if target.exists():
        if not target.is_dir():
            raise InitProjectError(f"Target is not a directory: {target}")
        if any(target.iterdir()):
            raise InitProjectError(
                f"Target directory is not empty: {target.resolve()}\n"
                "Choose an empty directory or a new directory."
            )
        return _fill_empty(target, agent_type, model)
    return _create_new(target, agent_type, model)


def _validate_agent(agent_type: str, model: str | None) -> None:
    try:
        AgentConfig(type=agent_type, model=model)
    except ValidationError as exc:
        message = exc.errors()[0]["msg"]
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        raise InitProjectError(message) from exc


def _create_new(target: Path, agent_type: str, model: str | None) -> Path:
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
    try:
        _populate(staging, agent_type, model)
        os.rename(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _fill_empty(target: Path, agent_type: str, model: str | None) -> Path:
    created: list[Path] = []
    try:
        _populate(target, agent_type, model, created)
    except Exception:
        _remove_created(created)
        raise
    return target


def _populate(
    root: Path,
    agent_type: str,
    model: str | None,
    created: list[Path] | None = None,
) -> None:
    files = {
        "agentdocs.yaml": _config_text(agent_type, model),
        "README.md": _project_readme(agent_type, model),
        "docs/getting-started.md": _docs_text(),
        "benchmark/starter/README.md": _starter_readme(),
        "benchmark/verifiers/first_task/check.py": _verifier_text(),
    }
    for relative, contents in files.items():
        path = root / relative
        _ensure_parent(path.parent, root, created)
        if created is not None:
            created.append(path)
        path.write_text(contents, encoding="utf-8")


def _ensure_parent(directory: Path, root: Path, created: list[Path] | None) -> None:
    if created is None:
        directory.mkdir(parents=True, exist_ok=True)
        return
    pending: list[Path] = []
    current = directory
    while current != root and not current.exists():
        pending.append(current)
        current = current.parent
    for path in reversed(pending):
        path.mkdir()
        created.append(path)


def _remove_created(created: list[Path]) -> None:
    for path in reversed(created):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _config_text(agent_type: str, model: str | None) -> str:
    model_line = f"  model: {model}\n" if model is not None else ""
    return (
        "version: 1\n"
        "\n"
        "docs: ./docs\n"
        "starter: ./benchmark/starter\n"
        "\n"
        "agent:\n"
        f"  type: {agent_type}\n"
        f"{model_line}"
        "\n"
        "tasks:\n"
        f"  - id: {_TASK_ID}\n"
        "    prompt: |\n"
        "      Using the provided documentation, complete the requested task.\n"
        "\n"
        "    verify:\n"
        "      path: ./benchmark/verifiers/first_task\n"
        "      command: python3 check.py\n"
    )


def _docs_text() -> str:
    return (
        "# Getting started\n"
        "\n"
        "For the starter task, create a file named `result.txt`\n"
        "in the project root containing exactly:\n"
        "\n"
        f"{_EXPECTED}\n"
    )


def _starter_readme() -> str:
    return (
        "# Starter project\n"
        "\n"
        "Every task starts from a fresh copy of this directory.\n"
        "The coding agent is expected to edit this copy.\n"
    )


def _project_readme(agent_type: str, model: str | None) -> str:
    model_text = model if model is not None else "provider default"
    return (
        "# AgentDocsBench benchmark\n"
        "\n"
        f"Agent: `{agent_type}`\n"
        f"Model: `{model_text}`\n"
        "\n"
        "```bash\n"
        "agentdocs doctor --config agentdocs.yaml\n"
        "agentdocs test --config agentdocs.yaml\n"
        "```\n"
        "\n"
        "Install and authenticate that provider CLI before a real run.\n"
        "`agentdocs doctor` checks that the executable is on PATH. "
        "It does not log in and it does not run the benchmark.\n"
    )


def _verifier_text() -> str:
    return '''"""Require result.txt to contain HELLO_AGENTDOCS."""

import os
import sys
from pathlib import Path

EXPECTED = "HELLO_AGENTDOCS"


def main() -> int:
    root = os.environ.get("AGENTDOCS_PROJECT_DIR", "").strip()
    if not root:
        print("AGENTDOCS_PROJECT_DIR is not set", file=sys.stderr)
        return 1
    result = Path(root) / "result.txt"
    if not result.is_file():
        print("missing result.txt", file=sys.stderr)
        return 1
    text = result.read_text(encoding="utf-8").strip()
    if text != EXPECTED:
        print(f"result.txt did not contain {EXPECTED}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
