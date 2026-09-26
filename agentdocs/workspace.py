"""Create isolated temporary workspaces for AgentDocsBench tasks."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agentdocs._symlinks import find_symlink
from agentdocs.config import AgentDocsConfig, validate_config_paths


class WorkspaceError(RuntimeError):
    """Raised when a workspace cannot be created for a workspace-specific reason."""


@dataclass(frozen=True)
class Workspace:
    """An isolated copy of a starter project and its documentation.

    ``root`` is the temporary directory. ``project`` is the copy of
    ``config.starter``. ``docs`` is the copy of ``config.docs``.
    """

    root: Path
    project: Path
    docs: Path


@contextmanager
def create_workspace(config: AgentDocsConfig) -> Iterator[Workspace]:
    """Copy ``config.starter`` and ``config.docs`` into a temporary directory.

    The directory is removed when the ``with`` block ends, including when the
    block raises. The original directories are not modified.

    Symbolic links under either source are rejected. Filesystem errors from
    copying propagate unchanged. Missing source directories raise the same
    exceptions as :func:`validate_config_paths`.
    """
    validate_config_paths(config)
    _ensure_no_symlinks(config.starter)
    _ensure_no_symlinks(config.docs)

    with tempfile.TemporaryDirectory(prefix="agentdocs-") as directory:
        root = Path(directory)
        project = root / "project"
        docs = root / "docs"
        shutil.copytree(config.starter, project, symlinks=False)
        shutil.copytree(config.docs, docs, symlinks=False)
        yield Workspace(
            root=root.resolve(),
            project=project.resolve(),
            docs=docs.resolve(),
        )


def _ensure_no_symlinks(source: Path) -> None:
    link = find_symlink(source)
    if link is None:
        return
    raise WorkspaceError(
        "Workspace source contains symbolic link:\n"
        f"{link}\n\n"
        "Symbolic links are not supported in AgentDocsBench workspaces yet."
    )
