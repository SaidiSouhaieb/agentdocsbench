"""Record project entries changed by a coding agent.

The snapshot is the temporary project directory only. It is taken before the
agent runs and again after the agent process returns, before the verifier
runs. The manifest stores paths, types, sizes, and hashes. It does not store
file bytes.

This is not a Git diff, and it is not the benchmark fingerprint. The
fingerprint names the benchmark inputs. This manifest names what the agent
left in the project.

The after snapshot is the tree observed when ``agent.run()`` returned. A
background process that keeps writing after that return is outside this
record. Symbolic links are not followed.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agentdocs.content_hash import hash_file, sha256_bytes, sha256_canonical
from agentdocs.workspace import WorkspaceError

CHANGES_SCHEMA_VERSION = 1
CHANGES_ALGORITHM = "sha256"
_PATH_LIST_LIMIT = 20


class WorkspaceChangeError(WorkspaceError):
    """Raised when the project tree cannot be snapshotted safely.

    This is a workspace infrastructure failure. A task result is not produced,
    and the verifier is not run.
    """


@dataclass(frozen=True)
class WorkspaceEntry:
    """One project entry, relative to the project root."""

    path: str
    entry_type: str
    sha256: str | None = None
    size_bytes: int | None = None
    target_sha256: str | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """A content-free snapshot of ``workspace.project``."""

    sha256: str
    entries: tuple[WorkspaceEntry, ...]


@dataclass(frozen=True)
class AddedFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class DeletedFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ModifiedFile:
    path: str
    before_sha256: str
    before_size_bytes: int
    after_sha256: str
    after_size_bytes: int


@dataclass(frozen=True)
class TypeChange:
    """A path whose entry type changed. This is not a content edit."""

    path: str
    before_type: str
    after_type: str
    before_sha256: str | None = None
    before_size_bytes: int | None = None
    before_target_sha256: str | None = None
    after_sha256: str | None = None
    after_size_bytes: int | None = None
    after_target_sha256: str | None = None


@dataclass(frozen=True)
class SymlinkRecord:
    path: str
    target_sha256: str


@dataclass(frozen=True)
class ModifiedSymlink:
    path: str
    before_target_sha256: str
    after_target_sha256: str


@dataclass(frozen=True)
class SpecialRecord:
    path: str
    entry_type: str


@dataclass(frozen=True)
class WorkspaceChangeManifest:
    """Differences between the pre-agent and post-agent project snapshots."""

    schema_version: int
    algorithm: str
    before_sha256: str
    after_sha256: str
    added_files: tuple[AddedFile, ...]
    modified_files: tuple[ModifiedFile, ...]
    deleted_files: tuple[DeletedFile, ...]
    added_directories: tuple[str, ...]
    deleted_directories: tuple[str, ...]
    type_changes: tuple[TypeChange, ...]
    added_symlinks: tuple[SymlinkRecord, ...] = ()
    modified_symlinks: tuple[ModifiedSymlink, ...] = ()
    deleted_symlinks: tuple[SymlinkRecord, ...] = ()
    added_special: tuple[SpecialRecord, ...] = ()
    deleted_special: tuple[SpecialRecord, ...] = ()

    @property
    def changed(self) -> bool:
        return self.before_sha256 != self.after_sha256

    @property
    def file_change_count(self) -> int:
        return len(self.added_files) + len(self.modified_files) + len(self.deleted_files)


def capture_workspace_snapshot(project_dir: Path) -> WorkspaceSnapshot:
    """Hash ``project_dir`` without following links or reading special files."""
    root = Path(project_dir)
    if root.is_symlink():
        raise WorkspaceChangeError(
            f"Project directory is a symbolic link and will not be followed: {root}"
        )
    if not root.is_dir():
        raise WorkspaceChangeError(f"Project directory is not a directory: {root}")
    entries: list[WorkspaceEntry] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(dir_names):
            candidate = parent / name
            entry = _snapshot_entry(root, candidate)
            if entry.entry_type != "directory":
                dir_names.remove(name)
            entries.append(entry)
        for name in file_names:
            entries.append(_snapshot_entry(root, parent / name))
    ordered = tuple(sorted(entries, key=lambda item: item.path))
    return WorkspaceSnapshot(sha256=_snapshot_digest(ordered), entries=ordered)


def compare_workspace_snapshots(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
) -> WorkspaceChangeManifest:
    """Classify project changes. A rename is a deletion plus an addition."""
    before_entries = {entry.path: entry for entry in before.entries}
    after_entries = {entry.path: entry for entry in after.entries}
    added_files: list[AddedFile] = []
    modified_files: list[ModifiedFile] = []
    deleted_files: list[DeletedFile] = []
    added_directories: list[str] = []
    deleted_directories: list[str] = []
    type_changes: list[TypeChange] = []
    added_symlinks: list[SymlinkRecord] = []
    modified_symlinks: list[ModifiedSymlink] = []
    deleted_symlinks: list[SymlinkRecord] = []
    added_special: list[SpecialRecord] = []
    deleted_special: list[SpecialRecord] = []
    for path in sorted(set(before_entries) | set(after_entries)):
        previous = before_entries.get(path)
        current = after_entries.get(path)
        if previous is None and current is not None:
            _collect_added(
                current,
                added_files,
                added_directories,
                added_symlinks,
                added_special,
            )
            continue
        if previous is not None and current is None:
            _collect_deleted(
                previous,
                deleted_files,
                deleted_directories,
                deleted_symlinks,
                deleted_special,
            )
            continue
        assert previous is not None and current is not None
        if previous.entry_type != current.entry_type:
            type_changes.append(_type_change(previous, current))
            continue
        if previous.entry_type == "file" and previous.sha256 != current.sha256:
            modified_files.append(
                ModifiedFile(
                    path=path,
                    before_sha256=_required_text(previous.sha256),
                    before_size_bytes=_required_size(previous.size_bytes),
                    after_sha256=_required_text(current.sha256),
                    after_size_bytes=_required_size(current.size_bytes),
                )
            )
        elif (
            previous.entry_type == "symlink"
            and previous.target_sha256 != current.target_sha256
        ):
            modified_symlinks.append(
                ModifiedSymlink(
                    path=path,
                    before_target_sha256=_required_text(previous.target_sha256),
                    after_target_sha256=_required_text(current.target_sha256),
                )
            )
    return WorkspaceChangeManifest(
        schema_version=CHANGES_SCHEMA_VERSION,
        algorithm=CHANGES_ALGORITHM,
        before_sha256=before.sha256,
        after_sha256=after.sha256,
        added_files=tuple(added_files),
        modified_files=tuple(modified_files),
        deleted_files=tuple(deleted_files),
        added_directories=tuple(added_directories),
        deleted_directories=tuple(deleted_directories),
        type_changes=tuple(type_changes),
        added_symlinks=tuple(added_symlinks),
        modified_symlinks=tuple(modified_symlinks),
        deleted_symlinks=tuple(deleted_symlinks),
        added_special=tuple(added_special),
        deleted_special=tuple(deleted_special),
    )


def empty_workspace_changes() -> WorkspaceChangeManifest:
    """A manifest for a result that recorded no project edits."""
    snapshot = WorkspaceSnapshot(sha256=_snapshot_digest(()), entries=())
    return compare_workspace_snapshots(snapshot, snapshot)


def file_change_label(manifest: WorkspaceChangeManifest) -> str:
    """Compact file counts: added, modified, deleted."""
    return (
        f"+{len(manifest.added_files)} "
        f"~{len(manifest.modified_files)} "
        f"-{len(manifest.deleted_files)}"
    )


def change_path_lines(manifest: WorkspaceChangeManifest) -> tuple[str, ...]:
    """Short path lines for verbose progress. File contents are not included."""
    lines = [f"+ {item.path}" for item in manifest.added_files]
    lines.extend(f"~ {item.path}" for item in manifest.modified_files)
    lines.extend(f"- {item.path}" for item in manifest.deleted_files)
    lines.extend(f"+dir {path}" for path in manifest.added_directories)
    lines.extend(f"-dir {path}" for path in manifest.deleted_directories)
    lines.extend(
        f"type {item.path}: {item.before_type} -> {item.after_type}"
        for item in manifest.type_changes
    )
    lines.extend(f"+link {item.path}" for item in manifest.added_symlinks)
    lines.extend(f"~link {item.path}" for item in manifest.modified_symlinks)
    lines.extend(f"-link {item.path}" for item in manifest.deleted_symlinks)
    lines.extend(f"+{item.entry_type} {item.path}" for item in manifest.added_special)
    lines.extend(f"-{item.entry_type} {item.path}" for item in manifest.deleted_special)
    return tuple(lines)


def capped_change_paths(
    manifest: WorkspaceChangeManifest,
    *,
    limit: int = _PATH_LIST_LIMIT,
) -> tuple[tuple[str, ...], int]:
    """Return at most ``limit`` path lines and how many were left out."""
    lines = change_path_lines(manifest)
    if len(lines) <= limit:
        return lines, 0
    return lines[:limit], len(lines) - limit


def changes_document(manifest: WorkspaceChangeManifest) -> dict[str, object]:
    """Serialize a manifest for ``changes.json``. File bytes are omitted."""
    return {
        "schema_version": manifest.schema_version,
        "algorithm": manifest.algorithm,
        "before_sha256": manifest.before_sha256,
        "after_sha256": manifest.after_sha256,
        "summary": {
            "files_added": len(manifest.added_files),
            "files_modified": len(manifest.modified_files),
            "files_deleted": len(manifest.deleted_files),
            "directories_added": len(manifest.added_directories),
            "directories_deleted": len(manifest.deleted_directories),
            "type_changed": len(manifest.type_changes),
            "symlinks_added": len(manifest.added_symlinks),
            "symlinks_modified": len(manifest.modified_symlinks),
            "symlinks_deleted": len(manifest.deleted_symlinks),
            "special_added": len(manifest.added_special),
            "special_deleted": len(manifest.deleted_special),
        },
        "files": {
            "added": [
                {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                for item in manifest.added_files
            ],
            "modified": [
                {
                    "path": item.path,
                    "before": {
                        "sha256": item.before_sha256,
                        "size_bytes": item.before_size_bytes,
                    },
                    "after": {
                        "sha256": item.after_sha256,
                        "size_bytes": item.after_size_bytes,
                    },
                }
                for item in manifest.modified_files
            ],
            "deleted": [
                {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                for item in manifest.deleted_files
            ],
        },
        "directories": {
            "added": list(manifest.added_directories),
            "deleted": list(manifest.deleted_directories),
        },
        "type_changes": [
            {
                "path": item.path,
                "before": _side(
                    item.before_type,
                    item.before_sha256,
                    item.before_size_bytes,
                    item.before_target_sha256,
                ),
                "after": _side(
                    item.after_type,
                    item.after_sha256,
                    item.after_size_bytes,
                    item.after_target_sha256,
                ),
            }
            for item in manifest.type_changes
        ],
        "symlinks": {
            "added": [
                {"path": item.path, "target_sha256": item.target_sha256}
                for item in manifest.added_symlinks
            ],
            "modified": [
                {
                    "path": item.path,
                    "before": {"target_sha256": item.before_target_sha256},
                    "after": {"target_sha256": item.after_target_sha256},
                }
                for item in manifest.modified_symlinks
            ],
            "deleted": [
                {"path": item.path, "target_sha256": item.target_sha256}
                for item in manifest.deleted_symlinks
            ],
        },
        "special": {
            "added": [
                {"path": item.path, "type": item.entry_type} for item in manifest.added_special
            ],
            "deleted": [
                {"path": item.path, "type": item.entry_type} for item in manifest.deleted_special
            ],
        },
    }


def _snapshot_entry(root: Path, candidate: Path) -> WorkspaceEntry:
    relative = _relative_path(root, candidate)
    kind = _entry_kind(candidate)
    if kind == "symlink":
        return WorkspaceEntry(
            path=relative,
            entry_type="symlink",
            target_sha256=_link_target_sha256(candidate),
        )
    if kind == "directory":
        return WorkspaceEntry(path=relative, entry_type="directory")
    if kind == "file":
        try:
            digest, size = hash_file(candidate)
        except OSError as exc:
            raise WorkspaceChangeError(f"Could not read project file {relative}: {exc}") from exc
        return WorkspaceEntry(
            path=relative,
            entry_type="file",
            sha256=digest,
            size_bytes=size,
        )
    return WorkspaceEntry(path=relative, entry_type=kind)


def _entry_kind(candidate: Path) -> str:
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        raise WorkspaceChangeError(f"Could not inspect project entry {candidate}: {exc}") from exc
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "char_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "other"


def _link_target_sha256(candidate: Path) -> str:
    try:
        target = os.readlink(candidate)
        encoded = os.fsencode(target)
    except OSError as exc:
        raise WorkspaceChangeError(
            f"Could not read symbolic link {candidate}: {exc}"
        ) from exc
    return sha256_bytes(encoded)


def _relative_path(root: Path, candidate: Path) -> str:
    relative = candidate.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise WorkspaceChangeError(f"Project path is not relative: {relative!r}")
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceChangeError(
            f"Workspace path cannot be represented in JSON: {relative!r}"
        ) from exc
    return relative


def _snapshot_digest(entries: tuple[WorkspaceEntry, ...]) -> str:
    return sha256_canonical({"entries": [_entry_document(entry) for entry in entries]})


def _entry_document(entry: WorkspaceEntry) -> dict[str, object]:
    document: dict[str, object] = {"path": entry.path, "type": entry.entry_type}
    if entry.sha256 is not None:
        document["sha256"] = entry.sha256
    if entry.size_bytes is not None:
        document["size_bytes"] = entry.size_bytes
    if entry.target_sha256 is not None:
        document["target_sha256"] = entry.target_sha256
    return document


def _collect_added(
    entry: WorkspaceEntry,
    files: list[AddedFile],
    directories: list[str],
    symlinks: list[SymlinkRecord],
    special: list[SpecialRecord],
) -> None:
    if entry.entry_type == "file":
        files.append(
            AddedFile(
                path=entry.path,
                sha256=_required_text(entry.sha256),
                size_bytes=_required_size(entry.size_bytes),
            )
        )
        return
    if entry.entry_type == "directory":
        directories.append(entry.path)
        return
    if entry.entry_type == "symlink":
        symlinks.append(
            SymlinkRecord(path=entry.path, target_sha256=_required_text(entry.target_sha256))
        )
        return
    special.append(SpecialRecord(path=entry.path, entry_type=entry.entry_type))


def _collect_deleted(
    entry: WorkspaceEntry,
    files: list[DeletedFile],
    directories: list[str],
    symlinks: list[SymlinkRecord],
    special: list[SpecialRecord],
) -> None:
    if entry.entry_type == "file":
        files.append(
            DeletedFile(
                path=entry.path,
                sha256=_required_text(entry.sha256),
                size_bytes=_required_size(entry.size_bytes),
            )
        )
        return
    if entry.entry_type == "directory":
        directories.append(entry.path)
        return
    if entry.entry_type == "symlink":
        symlinks.append(
            SymlinkRecord(path=entry.path, target_sha256=_required_text(entry.target_sha256))
        )
        return
    special.append(SpecialRecord(path=entry.path, entry_type=entry.entry_type))


def _type_change(before: WorkspaceEntry, after: WorkspaceEntry) -> TypeChange:
    return TypeChange(
        path=before.path,
        before_type=before.entry_type,
        after_type=after.entry_type,
        before_sha256=before.sha256,
        before_size_bytes=before.size_bytes,
        before_target_sha256=before.target_sha256,
        after_sha256=after.sha256,
        after_size_bytes=after.size_bytes,
        after_target_sha256=after.target_sha256,
    )


def _side(
    entry_type: str,
    digest: str | None,
    size: int | None,
    target: str | None,
) -> dict[str, object]:
    document: dict[str, object] = {"type": entry_type}
    if digest is not None:
        document["sha256"] = digest
    if size is not None:
        document["size_bytes"] = size
    if target is not None:
        document["target_sha256"] = target
    return document


def _required_text(value: str | None) -> str:
    if value is None:
        raise WorkspaceChangeError("Workspace entry is missing a required hash.")
    return value


def _required_size(value: int | None) -> int:
    if value is None:
        raise WorkspaceChangeError("Workspace entry is missing a required size.")
    return value
