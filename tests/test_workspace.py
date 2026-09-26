from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentdocs import WorkspaceError, create_workspace
from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig


def _config(docs: Path, starter: Path) -> AgentDocsConfig:
    verifier = starter.parent / "verifiers" / "create_user"
    verifier.mkdir(parents=True, exist_ok=True)
    return AgentDocsConfig(
        version=1,
        docs=docs,
        starter=starter,
        agent=AgentConfig(type="codex"),
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create a user named Alice.",
                verify=VerifyConfig(
                    path=verifier,
                    command="pytest tests/test_user.py",
                ),
            )
        ],
    )


def _make_sources(tmp_path: Path) -> tuple[Path, Path]:
    starter = tmp_path / "starter"
    docs = tmp_path / "docs"
    (starter / "src").mkdir(parents=True)
    (starter / "app.py").write_text('print("original")\n', encoding="utf-8")
    (starter / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (starter / ".env.example").write_text("TOKEN=example\n", encoding="utf-8")
    (starter / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (docs / "guides").mkdir(parents=True)
    (docs / "index.md").write_text("# Docs\n", encoding="utf-8")
    (docs / "guides" / "auth.md").write_text("# Auth\n", encoding="utf-8")
    return starter, docs


def _symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"This platform cannot create symlinks: {exc}")


def test_workspace_is_created(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert workspace.root.exists()
        assert workspace.project.exists()
        assert workspace.docs.exists()
        assert workspace.project == workspace.root / "project"
        assert workspace.docs == workspace.root / "docs"
        assert workspace.root.name.startswith("agentdocs-")


def test_starter_files_are_copied(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert (workspace.project / "app.py").is_file()
        assert (workspace.project / "src" / "example.py").is_file()


def test_docs_files_are_copied(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert (workspace.docs / "index.md").is_file()
        assert (workspace.docs / "guides" / "auth.md").is_file()


def test_file_contents_are_preserved(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert (workspace.project / "app.py").read_text(encoding="utf-8") == (
            starter / "app.py"
        ).read_text(encoding="utf-8")
        assert (workspace.docs / "guides" / "auth.md").read_text(encoding="utf-8") == (
            docs / "guides" / "auth.md"
        ).read_text(encoding="utf-8")


def test_hidden_files_are_copied(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert (workspace.project / ".env.example").read_text(encoding="utf-8") == (
            "TOKEN=example\n"
        )
        assert (workspace.project / ".gitignore").read_text(encoding="utf-8") == "*.pyc\n"


def test_original_starter_is_not_modified(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    original = (starter / "app.py").read_text(encoding="utf-8")
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        (workspace.project / "app.py").write_text('print("changed")\n', encoding="utf-8")

    assert (starter / "app.py").read_text(encoding="utf-8") == original


def test_original_docs_are_not_modified(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    original = (docs / "index.md").read_text(encoding="utf-8")
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        (workspace.docs / "index.md").write_text("# Changed\n", encoding="utf-8")

    assert (docs / "index.md").read_text(encoding="utf-8") == original


def test_workspace_paths_are_absolute(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert workspace.root.is_absolute()
        assert workspace.project.is_absolute()
        assert workspace.docs.is_absolute()


def test_workspace_is_cleaned_after_context_exit(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        root = workspace.root
        assert root.exists()

    assert not root.exists()


def test_workspace_is_cleaned_when_context_raises(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with pytest.raises(RuntimeError, match="boom"):
        with create_workspace(config) as workspace:
            root = workspace.root
            assert root.exists()
            raise RuntimeError("boom")

    assert not root.exists()


def test_missing_starter_directory_fails(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)
    shutil.rmtree(starter)

    with pytest.raises(FileNotFoundError, match="starter directory does not exist"):
        with create_workspace(config):
            pass


def test_missing_docs_directory_fails(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)
    shutil.rmtree(docs)

    with pytest.raises(FileNotFoundError, match="docs directory does not exist"):
        with create_workspace(config):
            pass


def test_symlink_inside_starter_is_rejected(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = starter / "nested" / "link.txt"
    _symlink(link, target)
    config = _config(docs, starter)

    with pytest.raises(WorkspaceError, match="Symbolic links are not supported") as exc_info:
        with create_workspace(config):
            pass

    assert str(link) in str(exc_info.value)


def test_symlink_inside_docs_is_rejected(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    target = tmp_path / "outside.md"
    target.write_text("# Outside\n", encoding="utf-8")
    link = docs / "guides" / "linked.md"
    _symlink(link, target)
    config = _config(docs, starter)

    with pytest.raises(WorkspaceError, match="Symbolic links are not supported") as exc_info:
        with create_workspace(config):
            pass

    assert str(link) in str(exc_info.value)


def test_nested_directories_are_copied(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    deep_starter = starter / "pkg" / "mod" / "deep.py"
    deep_docs = docs / "reference" / "api" / "users.md"
    deep_starter.parent.mkdir(parents=True)
    deep_docs.parent.mkdir(parents=True)
    deep_starter.write_text("deep\n", encoding="utf-8")
    deep_docs.write_text("# Users\n", encoding="utf-8")
    config = _config(docs, starter)

    with create_workspace(config) as workspace:
        assert (workspace.project / "pkg" / "mod" / "deep.py").read_text(
            encoding="utf-8"
        ) == "deep\n"
        assert (workspace.docs / "reference" / "api" / "users.md").read_text(
            encoding="utf-8"
        ) == "# Users\n"


def test_two_workspaces_are_independent(tmp_path: Path) -> None:
    starter, docs = _make_sources(tmp_path)
    config = _config(docs, starter)

    with create_workspace(config) as workspace1:
        (workspace1.project / "app.py").write_text('print("changed")\n', encoding="utf-8")
        first_root = workspace1.root

    with create_workspace(config) as workspace2:
        assert workspace2.root != first_root
        assert (workspace2.project / "app.py").read_text(encoding="utf-8") == (
            'print("original")\n'
        )
        assert (starter / "app.py").read_text(encoding="utf-8") == 'print("original")\n'
