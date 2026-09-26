from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentdocs.content_hash import sha256_bytes
from agentdocs.workspace_changes import (
    WorkspaceChangeError,
    capture_workspace_snapshot,
    changes_document,
    compare_workspace_snapshots,
)


def _pair(before: Path, after: Path):
    return compare_workspace_snapshots(
        capture_workspace_snapshot(before),
        capture_workspace_snapshot(after),
    )


def test_identical_trees_have_no_changes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_bytes(b"print(1)\n")
    other = tmp_path / "copy"
    other.mkdir()
    (other / "app.py").write_bytes(b"print(1)\n")

    manifest = _pair(root, other)

    assert manifest.changed is False
    assert manifest.before_sha256 == manifest.after_sha256
    assert manifest.added_files == ()
    assert manifest.modified_files == ()
    assert manifest.deleted_files == ()


def test_added_modified_deleted_and_sorted_paths(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "keep.py").write_bytes(b"same\n")
    (after / "keep.py").write_bytes(b"same\n")
    (before / "old.py").write_bytes(b"gone\n")
    (before / "edit.py").write_bytes(b"one\n")
    (after / "edit.py").write_bytes(b"two\n")
    (after / "c.py").write_bytes(b"c")
    (after / "a.py").write_bytes(b"a")
    (after / "b.py").write_bytes(b"\x00\xff")

    manifest = _pair(before, after)

    assert [item.path for item in manifest.added_files] == ["a.py", "b.py", "c.py"]
    assert manifest.added_files[1].size_bytes == 2
    assert [item.path for item in manifest.modified_files] == ["edit.py"]
    assert manifest.modified_files[0].before_sha256 != manifest.modified_files[0].after_sha256
    assert [item.path for item in manifest.deleted_files] == ["old.py"]
    text = json.dumps(changes_document(manifest))
    assert "gone" not in text
    assert "two" not in text
    assert str(tmp_path) not in text


def test_hidden_file_and_empty_directory(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (after / ".hidden").write_bytes(b"secret-body")
    (after / "generated").mkdir()
    (before / "removed").mkdir()

    manifest = _pair(before, after)

    assert [item.path for item in manifest.added_files] == [".hidden"]
    assert manifest.added_directories == ("generated",)
    assert manifest.deleted_directories == ("removed",)
    assert "secret-body" not in json.dumps(changes_document(manifest))


def test_mtime_does_not_count_as_a_modification(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"print(1)\n")
    first = capture_workspace_snapshot(root)
    os.utime(target, (1_700_000_000, 1_700_000_000))
    second = capture_workspace_snapshot(root)
    manifest = compare_workspace_snapshots(first, second)

    assert first.sha256 == second.sha256
    assert manifest.changed is False
    assert manifest.modified_files == ()


def test_rename_is_a_deletion_and_an_addition(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    payload = b"same-bytes\n"
    (before / "old.py").write_bytes(payload)
    (after / "new.py").write_bytes(payload)

    manifest = _pair(before, after)
    document = changes_document(manifest)

    assert [item.path for item in manifest.deleted_files] == ["old.py"]
    assert [item.path for item in manifest.added_files] == ["new.py"]
    assert manifest.deleted_files[0].sha256 == manifest.added_files[0].sha256
    assert "rename" not in document


def test_file_and_directory_type_changes(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "notes").write_bytes(b"file")
    (after / "notes").mkdir()
    (after / "notes" / "inner.txt").write_bytes(b"inside")
    (before / "pkg").mkdir()
    (before / "pkg" / "mod.py").write_bytes(b"mod")
    (after / "pkg").write_bytes(b"now-a-file")

    manifest = _pair(before, after)

    by_path = {item.path: item for item in manifest.type_changes}
    assert by_path["notes"].before_type == "file"
    assert by_path["notes"].after_type == "directory"
    assert by_path["pkg"].before_type == "directory"
    assert by_path["pkg"].after_type == "file"
    assert "notes" not in manifest.added_directories
    assert "notes" not in [item.path for item in manifest.deleted_files]
    assert [item.path for item in manifest.added_files] == ["notes/inner.txt"]
    assert [item.path for item in manifest.deleted_files] == ["pkg/mod.py"]


def test_snapshot_digest_is_stable_and_paths_are_relative(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "app"
    nested.mkdir(parents=True)
    (nested / "users.py").write_bytes(b"print(1)\n")
    first = capture_workspace_snapshot(root)
    second = capture_workspace_snapshot(root)
    document = json.dumps({"entries": [entry.path for entry in first.entries]})

    assert first.sha256 == second.sha256
    assert [entry.path for entry in first.entries] == ["app", "app/users.py"]
    assert str(root) not in document
    assert ".." not in document


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"SECRET_OUTSIDE_BYTES")
    (outside / "nested").mkdir()
    (outside / "nested" / "hidden.py").write_bytes(b"should-not-appear")
    link = root / "current"
    linked_dir = root / "linked-dir"
    try:
        link.symlink_to(secret)
        linked_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"This platform cannot create symlinks: {exc}")

    snapshot = capture_workspace_snapshot(root)
    text = json.dumps(changes_document(compare_workspace_snapshots(_empty(tmp_path), snapshot)))
    by_path = {entry.path: entry for entry in snapshot.entries}

    assert by_path["current"].entry_type == "symlink"
    assert by_path["current"].sha256 is None
    assert by_path["current"].target_sha256 == sha256_bytes(os.fsencode(os.readlink(link)))
    assert by_path["linked-dir"].entry_type == "symlink"
    assert "nested/hidden.py" not in {entry.path for entry in snapshot.entries}
    assert "SECRET_OUTSIDE_BYTES" not in text
    assert "should-not-appear" not in text
    assert str(secret) not in text


def test_fifo_is_not_read_as_a_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    fifo = root / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"This platform cannot create a fifo: {exc}")

    snapshot = capture_workspace_snapshot(root)

    assert snapshot.entries[0].entry_type == "fifo"
    assert snapshot.entries[0].sha256 is None
    manifest = compare_workspace_snapshots(_empty(tmp_path), snapshot)
    assert manifest.added_special[0].entry_type == "fifo"
    assert manifest.added_files == ()


def test_unreadable_file_raises(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "secret.bin"
    target.write_bytes(b"data")
    target.chmod(0)
    try:
        target.read_bytes()
    except OSError:
        pass
    else:
        pytest.skip("This platform still allows the owner to read a mode-0 file.")
    try:
        with pytest.raises(WorkspaceChangeError, match="Could not read"):
            capture_workspace_snapshot(root)
    finally:
        target.chmod(0o644)


def _empty(tmp_path: Path):
    empty = tmp_path / "empty-project"
    empty.mkdir()
    return capture_workspace_snapshot(empty)
