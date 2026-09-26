"""Deterministic identity for a benchmark definition.

The fingerprint covers documentation, the starter project, task ids, prompts,
verifier commands, task order, and verifier source trees. It does not cover
the agent, model, run result, clock, absolute path, or file metadata.

Fingerprint schema version 1 hashes raw file bytes. It ignores modification
times, ownership, and permission bits, so a copied tree matches and an
executable-bit-only edit does not. A fingerprint taken before a run is the
benchmark that was selected. It is not a lock against another process editing
those sources while the run is in progress.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agentdocs._symlinks import find_symlink
from agentdocs.config import AgentDocsConfig
from agentdocs.content_hash import hash_file, sha256_bytes, sha256_canonical

FINGERPRINT_SCHEMA_VERSION = 1
FINGERPRINT_ALGORITHM = "sha256"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class BenchmarkFingerprintError(RuntimeError):
    """Raised when a benchmark definition cannot be fingerprinted.

    This is separate from an agent or verifier failure. Symbolic links,
    unreadable files, and unsupported filesystem entries use this error.
    """


@dataclass(frozen=True)
class FileManifestEntry:
    """One regular file, identified by a path relative to its tree root."""

    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class TreeManifest:
    """Sorted directories and files for one source tree, plus its digest."""

    sha256: str
    directories: tuple[str, ...]
    files: tuple[FileManifestEntry, ...]


@dataclass(frozen=True)
class TaskFingerprint:
    """Identity of one task definition. The prompt text is not stored."""

    id: str
    position: int
    prompt_sha256: str
    verifier_command_sha256: str


@dataclass(frozen=True)
class VerifierFingerprint:
    """Verifier source tree associated with one task id."""

    task_id: str
    tree: TreeManifest


@dataclass(frozen=True)
class BenchmarkFingerprint:
    """Summary digests and the manifest those digests were built from."""

    schema_version: int
    algorithm: str
    overall_sha256: str
    docs: TreeManifest
    starter: TreeManifest
    tasks_sha256: str
    tasks: tuple[TaskFingerprint, ...]
    verifiers_sha256: str
    verifiers: tuple[VerifierFingerprint, ...]

    @property
    def docs_sha256(self) -> str:
        return self.docs.sha256

    @property
    def starter_sha256(self) -> str:
        return self.starter.sha256


@dataclass(frozen=True)
class EntryTypeChange:
    """A path that is a file on one side and a directory on the other."""

    path: str
    before: str
    after: str


@dataclass(frozen=True)
class TreeChangeManifest:
    """Factual path changes between two trees. Renames stay add plus remove."""

    added_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    added_directories: tuple[str, ...]
    removed_directories: tuple[str, ...]
    type_changes: tuple[EntryTypeChange, ...]

    @property
    def empty(self) -> bool:
        return not (
            self.added_files
            or self.removed_files
            or self.modified_files
            or self.added_directories
            or self.removed_directories
            or self.type_changes
        )


@dataclass(frozen=True)
class TaskDefinitionChange:
    """One task id whose prompt or verifier command digest changed."""

    task_id: str
    prompt_changed: bool
    verifier_command_changed: bool


@dataclass(frozen=True)
class TaskChangeManifest:
    """Task-definition changes. Prompt text is not included."""

    added_task_ids: tuple[str, ...]
    removed_task_ids: tuple[str, ...]
    reordered: bool
    definition_changes: tuple[TaskDefinitionChange, ...]

    @property
    def prompt_changed(self) -> tuple[str, ...]:
        return tuple(item.task_id for item in self.definition_changes if item.prompt_changed)

    @property
    def verifier_command_changed(self) -> tuple[str, ...]:
        return tuple(
            item.task_id for item in self.definition_changes if item.verifier_command_changed
        )

    @property
    def empty(self) -> bool:
        return not (
            self.added_task_ids
            or self.removed_task_ids
            or self.reordered
            or self.definition_changes
        )


@dataclass(frozen=True)
class VerifierChangeManifest:
    """Source-tree changes for one task that exists on both sides."""

    task_id: str
    changes: TreeChangeManifest


@dataclass(frozen=True)
class BenchmarkChangeManifest:
    """What differs between two benchmark fingerprints."""

    overall_equal: bool
    docs: TreeChangeManifest
    starter: TreeChangeManifest
    tasks: TaskChangeManifest
    verifiers: tuple[VerifierChangeManifest, ...]


def compute_benchmark_fingerprint(config: AgentDocsConfig) -> BenchmarkFingerprint:
    """Hash the benchmark inputs on ``config``.

    Agent type and model are ignored. Paths are stored relative to each tree.
    Call this before executing a suite when the artifact must name the
    benchmark that was selected.
    """
    docs = _fingerprint_tree(config.docs, label="Docs")
    starter = _fingerprint_tree(config.starter, label="Starter")
    tasks = _task_fingerprints(config)
    verifiers = _verifier_fingerprints(config)
    tasks_sha256 = _tasks_digest(tasks)
    verifiers_sha256 = _verifiers_digest(verifiers)
    overall = _overall_digest(docs.sha256, starter.sha256, tasks_sha256, verifiers_sha256)
    return BenchmarkFingerprint(
        schema_version=FINGERPRINT_SCHEMA_VERSION,
        algorithm=FINGERPRINT_ALGORITHM,
        overall_sha256=overall,
        docs=docs,
        starter=starter,
        tasks_sha256=tasks_sha256,
        tasks=tasks,
        verifiers_sha256=verifiers_sha256,
        verifiers=verifiers,
    )


def compare_benchmark_fingerprints(
    before: BenchmarkFingerprint,
    after: BenchmarkFingerprint,
) -> BenchmarkChangeManifest:
    """Describe benchmark-input changes from ``before`` to ``after``.

    Added means present only in ``after``. Removed means present only in
    ``before``. This does not describe files an agent wrote.
    """
    shared = _shared_task_ids(before.tasks, after.tasks)
    return BenchmarkChangeManifest(
        overall_equal=before.overall_sha256 == after.overall_sha256,
        docs=_tree_changes(before.docs, after.docs),
        starter=_tree_changes(before.starter, after.starter),
        tasks=TaskChangeManifest(
            added_task_ids=_ids_only_in(after.tasks, before.tasks),
            removed_task_ids=_ids_only_in(before.tasks, after.tasks),
            reordered=_task_order(before.tasks, shared) != _task_order(after.tasks, shared),
            definition_changes=_definition_changes(before.tasks, after.tasks, shared),
        ),
        verifiers=_verifier_changes(before.verifiers, after.verifiers, shared),
    )


def fingerprint_summary(fingerprint: BenchmarkFingerprint) -> dict[str, object]:
    """Small identity block stored on a run or matrix artifact."""
    return {
        "fingerprint_schema_version": fingerprint.schema_version,
        "algorithm": fingerprint.algorithm,
        "overall_sha256": fingerprint.overall_sha256,
        "docs_sha256": fingerprint.docs_sha256,
        "starter_sha256": fingerprint.starter_sha256,
        "tasks_sha256": fingerprint.tasks_sha256,
        "verifiers_sha256": fingerprint.verifiers_sha256,
        "manifest_path": "benchmark.json",
    }


def fingerprint_manifest_document(fingerprint: BenchmarkFingerprint) -> dict[str, object]:
    """Detailed ``benchmark.json`` document. File contents are not included."""
    return {
        "schema_version": fingerprint.schema_version,
        "algorithm": fingerprint.algorithm,
        "overall_sha256": fingerprint.overall_sha256,
        "components": {
            "docs": _tree_document(fingerprint.docs),
            "starter": _tree_document(fingerprint.starter),
            "tasks": {
                "sha256": fingerprint.tasks_sha256,
                "items": [_task_document(task) for task in fingerprint.tasks],
            },
            "verifiers": {
                "sha256": fingerprint.verifiers_sha256,
                "items": [_verifier_document(item) for item in fingerprint.verifiers],
            },
        },
    }


def load_benchmark_manifest(path: str | Path) -> BenchmarkFingerprint:
    """Load ``benchmark.json`` and reject a manifest whose digests do not match."""
    candidate = Path(path)
    if not candidate.is_file():
        raise BenchmarkFingerprintError(f"Benchmark manifest not found: {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkFingerprintError(
            f"Benchmark manifest is not valid JSON: {candidate}"
        ) from exc
    except OSError as exc:
        raise BenchmarkFingerprintError(
            f"Could not read benchmark manifest {candidate}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BenchmarkFingerprintError(f"Benchmark manifest must be a JSON object: {candidate}")
    return _fingerprint_from_document(candidate, payload)


def _fingerprint_tree(root: Path, *, label: str) -> TreeManifest:
    if root.is_symlink():
        raise BenchmarkFingerprintError(
            f"{label} contains a symbolic link: {root}. "
            "Symbolic links are not included in a benchmark fingerprint."
        )
    if not root.exists():
        raise BenchmarkFingerprintError(f"{label} directory does not exist: {root}")
    if not root.is_dir():
        raise BenchmarkFingerprintError(f"{label} path is not a directory: {root}")
    link = find_symlink(root)
    if link is not None:
        raise BenchmarkFingerprintError(
            f"{label} contains a symbolic link: {link}. "
            "Symbolic links are not included in a benchmark fingerprint."
        )
    directories: list[str] = []
    files: list[FileManifestEntry] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in dir_names:
            candidate = parent / name
            _require_real_directory(candidate, label)
            directories.append(candidate.relative_to(root).as_posix())
        for name in file_names:
            candidate = parent / name
            _require_regular_file(candidate, label)
            digest, size = _hash_file(candidate, label)
            files.append(
                FileManifestEntry(
                    relative_path=candidate.relative_to(root).as_posix(),
                    sha256=digest,
                    size_bytes=size,
                )
            )
    ordered_directories = tuple(sorted(directories))
    ordered_files = tuple(sorted(files, key=lambda item: item.relative_path))
    return TreeManifest(
        sha256=_tree_digest(ordered_directories, ordered_files),
        directories=ordered_directories,
        files=ordered_files,
    )


def _require_real_directory(candidate: Path, label: str) -> None:
    if candidate.is_symlink():
        raise BenchmarkFingerprintError(
            f"{label} contains a symbolic link: {candidate}. "
            "Symbolic links are not included in a benchmark fingerprint."
        )
    if not candidate.is_dir():
        raise BenchmarkFingerprintError(
            f"{label} contains an unsupported filesystem entry: {candidate}"
        )


def _require_regular_file(candidate: Path, label: str) -> None:
    if candidate.is_symlink():
        raise BenchmarkFingerprintError(
            f"{label} contains a symbolic link: {candidate}. "
            "Symbolic links are not included in a benchmark fingerprint."
        )
    if not candidate.is_file():
        raise BenchmarkFingerprintError(
            f"{label} contains an unsupported filesystem entry: {candidate}"
        )


def _hash_file(path: Path, label: str) -> tuple[str, int]:
    try:
        return hash_file(path)
    except OSError as exc:
        raise BenchmarkFingerprintError(f"Could not read {label} file {path}: {exc}") from exc


def _task_fingerprints(config: AgentDocsConfig) -> tuple[TaskFingerprint, ...]:
    seen: set[str] = set()
    tasks: list[TaskFingerprint] = []
    for position, task in enumerate(config.tasks):
        if task.id in seen:
            raise BenchmarkFingerprintError(f"Duplicate task id {task.id!r}.")
        seen.add(task.id)
        tasks.append(
            TaskFingerprint(
                id=task.id,
                position=position,
                prompt_sha256=_sha256_bytes(task.prompt.encode("utf-8")),
                verifier_command_sha256=_sha256_bytes(task.verify.command.encode("utf-8")),
            )
        )
    return tuple(tasks)


def _verifier_fingerprints(config: AgentDocsConfig) -> tuple[VerifierFingerprint, ...]:
    cached: dict[Path, TreeManifest] = {}
    items: list[VerifierFingerprint] = []
    for task in config.tasks:
        path = task.verify.path
        tree = cached.get(path)
        if tree is None:
            tree = _fingerprint_tree(path, label=f"Verifier {task.id}")
            cached[path] = tree
        items.append(VerifierFingerprint(task_id=task.id, tree=tree))
    return tuple(items)


def _tree_digest(directories: tuple[str, ...], files: tuple[FileManifestEntry, ...]) -> str:
    return _sha256_canonical(
        {
            "directories": list(directories),
            "files": [_file_document(item) for item in files],
        }
    )


def _tasks_digest(tasks: tuple[TaskFingerprint, ...]) -> str:
    return _sha256_canonical([_task_document(task) for task in tasks])


def _verifiers_digest(verifiers: tuple[VerifierFingerprint, ...]) -> str:
    return _sha256_canonical(
        [{"sha256": item.tree.sha256, "task_id": item.task_id} for item in verifiers]
    )


def _overall_digest(
    docs_sha256: str,
    starter_sha256: str,
    tasks_sha256: str,
    verifiers_sha256: str,
) -> str:
    return _sha256_canonical(
        {
            "docs_sha256": docs_sha256,
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            "starter_sha256": starter_sha256,
            "tasks_sha256": tasks_sha256,
            "verifiers_sha256": verifiers_sha256,
        }
    )


def _sha256_bytes(data: bytes) -> str:
    return sha256_bytes(data)


def _sha256_canonical(value: object) -> str:
    return sha256_canonical(value)


def _tree_document(tree: TreeManifest) -> dict[str, object]:
    return {
        "sha256": tree.sha256,
        "directories": list(tree.directories),
        "files": [_file_document(item) for item in tree.files],
    }


def _file_document(item: FileManifestEntry) -> dict[str, object]:
    return {
        "path": item.relative_path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
    }


def _task_document(task: TaskFingerprint) -> dict[str, object]:
    return {
        "id": task.id,
        "position": task.position,
        "prompt_sha256": task.prompt_sha256,
        "verifier_command_sha256": task.verifier_command_sha256,
    }


def _verifier_document(item: VerifierFingerprint) -> dict[str, object]:
    return {
        "task_id": item.task_id,
        "sha256": item.tree.sha256,
        "directories": list(item.tree.directories),
        "files": [_file_document(entry) for entry in item.tree.files],
    }


def _fingerprint_from_document(path: Path, payload: dict[str, Any]) -> BenchmarkFingerprint:
    schema_version = payload.get("schema_version")
    if schema_version != FINGERPRINT_SCHEMA_VERSION:
        raise BenchmarkFingerprintError(
            f"Unsupported benchmark manifest schema {schema_version!r} in {path}."
        )
    if payload.get("algorithm") != FINGERPRINT_ALGORITHM:
        raise BenchmarkFingerprintError(f"Unsupported fingerprint algorithm in {path}.")
    components = payload.get("components")
    if not isinstance(components, dict):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing components: {path}")
    docs = _tree_from_document(path, components.get("docs"), label="docs")
    starter = _tree_from_document(path, components.get("starter"), label="starter")
    tasks_sha256, tasks = _tasks_from_document(path, components.get("tasks"))
    verifiers_sha256, verifiers = _verifiers_from_document(path, components.get("verifiers"))
    overall = _require_sha256(payload.get("overall_sha256"), path, "overall_sha256")
    expected = _overall_digest(docs.sha256, starter.sha256, tasks_sha256, verifiers_sha256)
    if overall != expected:
        raise BenchmarkFingerprintError(
            f"Benchmark manifest digest does not match its contents: {path}"
        )
    return BenchmarkFingerprint(
        schema_version=FINGERPRINT_SCHEMA_VERSION,
        algorithm=FINGERPRINT_ALGORITHM,
        overall_sha256=overall,
        docs=docs,
        starter=starter,
        tasks_sha256=tasks_sha256,
        tasks=tasks,
        verifiers_sha256=verifiers_sha256,
        verifiers=verifiers,
    )


def _tree_from_document(path: Path, payload: object, *, label: str) -> TreeManifest:
    if not isinstance(payload, dict):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing {label}: {path}")
    directories = _relative_paths(path, payload.get("directories"), label=f"{label}.directories")
    files = _files_from_document(path, payload.get("files"), label=f"{label}.files")
    ordered_directories = tuple(sorted(directories))
    ordered_files = tuple(sorted(files, key=lambda item: item.relative_path))
    if len(ordered_directories) != len(set(ordered_directories)):
        raise BenchmarkFingerprintError(f"Duplicate directory path in {path}.")
    digest = _tree_digest(ordered_directories, ordered_files)
    stored = _require_sha256(payload.get("sha256"), path, f"{label}.sha256")
    if stored != digest:
        raise BenchmarkFingerprintError(
            f"Benchmark manifest digest does not match its contents: {path}"
        )
    return TreeManifest(sha256=digest, directories=ordered_directories, files=ordered_files)


def _files_from_document(path: Path, payload: object, *, label: str) -> tuple[FileManifestEntry, ...]:
    if not isinstance(payload, list):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing {label}: {path}")
    files: list[FileManifestEntry] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise BenchmarkFingerprintError(f"File entry must be an object in {path}.")
        relative = _require_relative(item.get("path"), path)
        if relative in seen:
            raise BenchmarkFingerprintError(f"Duplicate file path {relative!r} in {path}.")
        seen.add(relative)
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BenchmarkFingerprintError(f"File size is invalid for {relative!r} in {path}.")
        files.append(
            FileManifestEntry(
                relative_path=relative,
                sha256=_require_sha256(item.get("sha256"), path, relative),
                size_bytes=size,
            )
        )
    return tuple(files)


def _tasks_from_document(
    path: Path,
    payload: object,
) -> tuple[str, tuple[TaskFingerprint, ...]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing tasks: {path}")
    tasks: list[TaskFingerprint] = []
    seen: set[str] = set()
    for index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            raise BenchmarkFingerprintError(f"Task entry must be an object in {path}.")
        task_id = item.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise BenchmarkFingerprintError(f"Task id is invalid in {path}.")
        if task_id in seen:
            raise BenchmarkFingerprintError(f"Duplicate task id {task_id!r} in {path}.")
        seen.add(task_id)
        position = item.get("position")
        if position != index:
            raise BenchmarkFingerprintError(
                f"Task {task_id!r} position does not match manifest order in {path}."
            )
        tasks.append(
            TaskFingerprint(
                id=task_id,
                position=index,
                prompt_sha256=_require_sha256(item.get("prompt_sha256"), path, task_id),
                verifier_command_sha256=_require_sha256(
                    item.get("verifier_command_sha256"),
                    path,
                    task_id,
                ),
            )
        )
    ordered = tuple(tasks)
    digest = _tasks_digest(ordered)
    stored = _require_sha256(payload.get("sha256"), path, "tasks.sha256")
    if stored != digest:
        raise BenchmarkFingerprintError(
            f"Benchmark manifest digest does not match its contents: {path}"
        )
    return digest, ordered


def _verifiers_from_document(
    path: Path,
    payload: object,
) -> tuple[str, tuple[VerifierFingerprint, ...]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing verifiers: {path}")
    items: list[VerifierFingerprint] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise BenchmarkFingerprintError(f"Verifier entry must be an object in {path}.")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise BenchmarkFingerprintError(f"Verifier task id is invalid in {path}.")
        directories = _relative_paths(
            path,
            item.get("directories"),
            label=f"verifier {task_id} directories",
        )
        files = _files_from_document(path, item.get("files"), label=f"verifier {task_id} files")
        tree = TreeManifest(
            sha256=_tree_digest(tuple(sorted(directories)), tuple(sorted(files, key=lambda entry: entry.relative_path))),
            directories=tuple(sorted(directories)),
            files=tuple(sorted(files, key=lambda entry: entry.relative_path)),
        )
        stored = _require_sha256(item.get("sha256"), path, task_id)
        if stored != tree.sha256:
            raise BenchmarkFingerprintError(
                f"Benchmark manifest digest does not match its contents: {path}"
            )
        items.append(VerifierFingerprint(task_id=task_id, tree=tree))
    ordered = tuple(items)
    digest = _verifiers_digest(ordered)
    stored = _require_sha256(payload.get("sha256"), path, "verifiers.sha256")
    if stored != digest:
        raise BenchmarkFingerprintError(
            f"Benchmark manifest digest does not match its contents: {path}"
        )
    return digest, ordered


def _relative_paths(path: Path, payload: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(payload, list):
        raise BenchmarkFingerprintError(f"Benchmark manifest is missing {label}: {path}")
    return tuple(_require_relative(item, path) for item in payload)


def _require_relative(value: object, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkFingerprintError(f"Manifest path is invalid in {path}.")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise BenchmarkFingerprintError(f"Manifest path must be relative: {value!r} in {path}.")
    return value


def _require_sha256(value: object, path: Path, label: str) -> str:
    if not isinstance(value, str) or _SHA256_HEX.fullmatch(value) is None:
        raise BenchmarkFingerprintError(f"Invalid sha256 for {label} in {path}.")
    return value


def _tree_changes(before: TreeManifest, after: TreeManifest) -> TreeChangeManifest:
    before_files = {item.relative_path: item for item in before.files}
    after_files = {item.relative_path: item for item in after.files}
    before_dirs = set(before.directories)
    after_dirs = set(after.directories)
    type_changes = [
        EntryTypeChange(path=path, before="file", after="directory")
        for path in sorted(set(before_files) & after_dirs)
    ]
    type_changes.extend(
        EntryTypeChange(path=path, before="directory", after="file")
        for path in sorted(set(after_files) & before_dirs)
    )
    type_paths = {change.path for change in type_changes}
    added_files = tuple(sorted(path for path in after_files if path not in before_files and path not in type_paths))
    removed_files = tuple(sorted(path for path in before_files if path not in after_files and path not in type_paths))
    modified = tuple(
        sorted(
            path
            for path, item in before_files.items()
            if path in after_files and item.sha256 != after_files[path].sha256
        )
    )
    added_directories = _meaningful_directories(
        sorted(path for path in after_dirs - before_dirs if path not in type_paths),
        added_files,
    )
    removed_directories = _meaningful_directories(
        sorted(path for path in before_dirs - after_dirs if path not in type_paths),
        removed_files,
    )
    return TreeChangeManifest(
        added_files=added_files,
        removed_files=removed_files,
        modified_files=modified,
        added_directories=added_directories,
        removed_directories=removed_directories,
        type_changes=tuple(type_changes),
    )


def _meaningful_directories(directories: list[str], explaining_files: tuple[str, ...]) -> tuple[str, ...]:
    """Keep empty-directory changes and omit parents that only hold reported files."""
    explained = [
        directory
        for directory in directories
        if not any(file_path.startswith(directory + "/") for file_path in explaining_files)
    ]
    return tuple(
        directory
        for directory in explained
        if not any(other.startswith(directory + "/") for other in explained if other != directory)
    )


def _shared_task_ids(
    before: tuple[TaskFingerprint, ...],
    after: tuple[TaskFingerprint, ...],
) -> tuple[str, ...]:
    after_ids = {task.id for task in after}
    return tuple(task.id for task in before if task.id in after_ids)


def _ids_only_in(
    source: tuple[TaskFingerprint, ...],
    other: tuple[TaskFingerprint, ...],
) -> tuple[str, ...]:
    other_ids = {task.id for task in other}
    return tuple(task.id for task in source if task.id not in other_ids)


def _task_order(tasks: tuple[TaskFingerprint, ...], shared: tuple[str, ...]) -> tuple[str, ...]:
    shared_ids = set(shared)
    return tuple(task.id for task in tasks if task.id in shared_ids)


def _definition_changes(
    before: tuple[TaskFingerprint, ...],
    after: tuple[TaskFingerprint, ...],
    shared: tuple[str, ...],
) -> tuple[TaskDefinitionChange, ...]:
    before_by_id = {task.id: task for task in before}
    after_by_id = {task.id: task for task in after}
    changes: list[TaskDefinitionChange] = []
    for task_id in shared:
        prompt_changed = before_by_id[task_id].prompt_sha256 != after_by_id[task_id].prompt_sha256
        command_changed = (
            before_by_id[task_id].verifier_command_sha256
            != after_by_id[task_id].verifier_command_sha256
        )
        if not prompt_changed and not command_changed:
            continue
        changes.append(
            TaskDefinitionChange(
                task_id=task_id,
                prompt_changed=prompt_changed,
                verifier_command_changed=command_changed,
            )
        )
    return tuple(changes)


def _verifier_changes(
    before: tuple[VerifierFingerprint, ...],
    after: tuple[VerifierFingerprint, ...],
    shared: tuple[str, ...],
) -> tuple[VerifierChangeManifest, ...]:
    before_by_id = {item.task_id: item for item in before}
    after_by_id = {item.task_id: item for item in after}
    changes: list[VerifierChangeManifest] = []
    for task_id in shared:
        if task_id not in before_by_id or task_id not in after_by_id:
            continue
        tree_changes = _tree_changes(before_by_id[task_id].tree, after_by_id[task_id].tree)
        if tree_changes.empty:
            continue
        changes.append(VerifierChangeManifest(task_id=task_id, changes=tree_changes))
    return tuple(changes)
