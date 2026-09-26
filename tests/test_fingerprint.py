from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agentdocs.config import AgentConfig, AgentDocsConfig, TaskConfig, VerifyConfig, load_config
from agentdocs.fingerprint import (
    BenchmarkFingerprintError,
    compare_benchmark_fingerprints,
    compute_benchmark_fingerprint,
    fingerprint_manifest_document,
    load_benchmark_manifest,
)

_SHA256 = r"^[0-9a-f]{64}$"


def _layout(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "starter").mkdir()
    (root / "verifiers" / "create_user").mkdir(parents=True)
    (root / "verifiers" / "create_project").mkdir(parents=True)
    (root / "docs" / "users.md").write_bytes(b"users\n")
    (root / "starter" / "main.py").write_bytes(b"print(1)\n")
    (root / "verifiers" / "create_user" / "check.py").write_bytes(b"raise SystemExit(0)\n")
    (root / "verifiers" / "create_project" / "check.py").write_bytes(b"raise SystemExit(0)\n")


def _config(
    root: Path,
    *,
    agent: str = "cursor",
    model: str | None = "model-a",
    tasks: list[TaskConfig] | None = None,
) -> AgentDocsConfig:
    if tasks is None:
        tasks = [
            TaskConfig(
                id="create_user",
                prompt="Create Alice.",
                verify=VerifyConfig(
                    path=root / "verifiers" / "create_user",
                    command="python3 check.py",
                ),
            ),
            TaskConfig(
                id="create_project",
                prompt="Create the project.",
                verify=VerifyConfig(
                    path=root / "verifiers" / "create_project",
                    command="python3 check.py",
                ),
            ),
        ]
    return AgentDocsConfig(
        version=1,
        docs=root / "docs",
        starter=root / "starter",
        agent=AgentConfig(type=agent, model=model),
        tasks=tasks,
    )


def _fingerprint(root: Path, **kwargs: object):
    return compute_benchmark_fingerprint(_config(root, **kwargs))  # type: ignore[arg-type]


def test_simple_example_fingerprint_is_unchanged() -> None:
    codex = compute_benchmark_fingerprint(load_config("examples/simple/agentdocs.yaml"))
    cursor = compute_benchmark_fingerprint(load_config("examples/simple/agentdocs.cursor.yaml"))

    assert codex.overall_sha256 == cursor.overall_sha256
    assert codex.overall_sha256 == (
        "9e272f284347203a83a7647fbd35c37e2a2e5ee4d0249f0e68b6c21b5d6a9f8b"
    )


def test_identical_benchmarks_match(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)

    assert _fingerprint(root).overall_sha256 == _fingerprint(root).overall_sha256


def test_another_absolute_path_matches(tmp_path: Path) -> None:
    source = tmp_path / "a" / "benchmark"
    _layout(source)
    copied = tmp_path / "b" / "benchmark"
    shutil.copytree(source, copied)

    assert _fingerprint(source).overall_sha256 == _fingerprint(copied).overall_sha256


def test_mtime_and_permissions_do_not_change_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    target = root / "docs" / "users.md"
    os.utime(target, (1_700_000_000, 1_700_000_000))
    os.chmod(target, 0o755)

    assert _fingerprint(root).overall_sha256 == original.overall_sha256


def test_agent_type_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)

    assert _fingerprint(root, agent="cursor").overall_sha256 == _fingerprint(root, agent="codex").overall_sha256


def test_model_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)

    assert (
        _fingerprint(root, model="model-a").overall_sha256
        == _fingerprint(root, model="model-b").overall_sha256
    )
    assert _fingerprint(root, model="model-a").overall_sha256 == _fingerprint(root, model=None).overall_sha256


def test_docs_byte_change_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    (root / "docs" / "users.md").write_bytes(b"users\nchanged")

    changed = _fingerprint(root)
    assert changed.overall_sha256 != original.overall_sha256
    assert changed.docs_sha256 != original.docs_sha256
    assert changed.starter_sha256 == original.starter_sha256


def test_added_and_removed_docs_files_change_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    (root / "docs" / "extra.md").write_bytes(b"extra")
    added = _fingerprint(root)
    assert added.overall_sha256 != original.overall_sha256
    (root / "docs" / "extra.md").unlink()
    (root / "docs" / "users.md").unlink()

    assert _fingerprint(root).overall_sha256 != original.overall_sha256


def test_docs_rename_changes_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    (root / "docs" / "users.md").rename(root / "docs" / "people.md")
    changed = _fingerprint(root)
    manifest = compare_benchmark_fingerprints(original, changed)

    assert changed.overall_sha256 != original.overall_sha256
    assert manifest.docs.removed_files == ("users.md",)
    assert manifest.docs.added_files == ("people.md",)
    assert manifest.docs.modified_files == ()


def test_hidden_and_binary_docs_files_are_included(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    (root / "docs" / ".hidden-config").write_bytes(b"hidden")
    (root / "docs" / "blob.bin").write_bytes(b"\x00\xff\xfe")
    fingerprint = _fingerprint(root)
    paths = [item.relative_path for item in fingerprint.docs.files]

    assert paths == [".hidden-config", "blob.bin", "users.md"]
    binary = fingerprint.docs.files[1]
    assert binary.size_bytes == 3
    assert binary.sha256


def test_file_manifest_is_sorted(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    (root / "docs" / "z.md").write_bytes(b"z")
    (root / "docs" / "a.md").write_bytes(b"a")

    assert [item.relative_path for item in _fingerprint(root).docs.files] == [
        "a.md",
        "users.md",
        "z.md",
    ]


def test_starter_byte_change_and_empty_directory(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    (root / "starter" / "main.py").write_bytes(b"print(2)\n")
    changed = _fingerprint(root)
    assert changed.starter_sha256 != original.starter_sha256
    (root / "starter" / "nested" / "leaf").mkdir(parents=True)
    with_directory = _fingerprint(root)
    manifest = compare_benchmark_fingerprints(changed, with_directory)

    assert with_directory.starter_sha256 != changed.starter_sha256
    assert manifest.starter.added_directories == ("nested/leaf",)


def test_task_prompt_id_order_and_command_change_the_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    original = _fingerprint(root)
    user, project = _config(root).tasks
    prompted = _config(root, tasks=[user.model_copy(update={"prompt": "Create Bob."}), project])
    renamed = _config(root, tasks=[user.model_copy(update={"id": "create_account"}), project])
    reordered = _config(root, tasks=[project, user])
    commanded = _config(
        root,
        tasks=[
            user.model_copy(
                update={"verify": user.verify.model_copy(update={"command": "python3 check.py --strict"})}
            ),
            project,
        ],
    )

    assert compute_benchmark_fingerprint(prompted).overall_sha256 != original.overall_sha256
    assert compute_benchmark_fingerprint(renamed).overall_sha256 != original.overall_sha256
    assert compute_benchmark_fingerprint(reordered).overall_sha256 != original.overall_sha256
    assert compute_benchmark_fingerprint(commanded).overall_sha256 != original.overall_sha256
    assert compute_benchmark_fingerprint(reordered).tasks[0].id == "create_project"
    assert compute_benchmark_fingerprint(reordered).tasks[0].position == 0


def test_verifier_file_changes_are_tied_to_the_task(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    (root / "verifiers" / "create_user" / "only_user.txt").write_bytes(b"user")
    original = _fingerprint(root)
    by_task = {item.task_id: item for item in original.verifiers}
    assert [item.relative_path for item in by_task["create_user"].tree.files] == [
        "check.py",
        "only_user.txt",
    ]
    assert [item.relative_path for item in by_task["create_project"].tree.files] == ["check.py"]
    (root / "verifiers" / "create_user" / "check.py").write_bytes(b"changed\n")
    (root / "verifiers" / "create_user" / "fixtures.py").write_bytes(b"fixture")
    changed = _fingerprint(root)
    manifest = compare_benchmark_fingerprints(original, changed)

    assert changed.verifiers_sha256 != original.verifiers_sha256
    assert manifest.verifiers[0].task_id == "create_user"
    assert manifest.verifiers[0].changes.modified_files == ("check.py",)
    assert manifest.verifiers[0].changes.added_files == ("fixtures.py",)
    assert all(item.task_id != "create_project" for item in manifest.verifiers)


def test_shared_verifier_directory_stays_associated_with_each_task(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    shared = root / "verifiers" / "create_user"
    config = _config(
        root,
        tasks=[
            TaskConfig(
                id="create_user",
                prompt="Create Alice.",
                verify=VerifyConfig(path=shared, command="python3 check.py"),
            ),
            TaskConfig(
                id="create_project",
                prompt="Create the project.",
                verify=VerifyConfig(path=shared, command="python3 check.py"),
            ),
        ],
    )
    fingerprint = compute_benchmark_fingerprint(config)

    assert [item.task_id for item in fingerprint.verifiers] == ["create_user", "create_project"]
    assert fingerprint.verifiers[0].tree.sha256 == fingerprint.verifiers[1].tree.sha256


def test_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    target = root / "docs" / "users.md"
    link = root / "docs" / "linked.md"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"This platform cannot create symlinks: {exc}")

    with pytest.raises(BenchmarkFingerprintError, match="symbolic link"):
        _fingerprint(root)


def test_unsupported_fifo_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    fifo = root / "docs" / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"This platform cannot create a fifo: {exc}")

    with pytest.raises(BenchmarkFingerprintError, match="unsupported filesystem entry"):
        _fingerprint(root)


def test_crlf_bytes_differ_from_lf(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _layout(left)
    shutil.copytree(left, right)
    (left / "docs" / "users.md").write_bytes(b"hello\n")
    (right / "docs" / "users.md").write_bytes(b"hello\r\n")

    assert _fingerprint(left).docs.files[0].sha256 != _fingerprint(right).docs.files[0].sha256


def test_digest_is_lowercase_sha256_and_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    _layout(root)
    fingerprint = _fingerprint(root)
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(fingerprint_manifest_document(fingerprint)), encoding="utf-8")
    loaded = load_benchmark_manifest(path)

    assert fingerprint.overall_sha256 == loaded.overall_sha256
    assert __import__("re").fullmatch(_SHA256, fingerprint.overall_sha256)
    assert "Create Alice." not in path.read_text(encoding="utf-8")
    assert str(root.resolve()) not in path.read_text(encoding="utf-8")


def test_change_manifest_classifies_files_and_tasks(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _layout(left)
    shutil.copytree(left, right)
    (right / "docs" / "added.md").write_bytes(b"new")
    (right / "docs" / "users.md").write_bytes(b"edited\n")
    (right / "starter" / "main.py").unlink()
    (left / "docs" / "notes").write_bytes(b"file")
    notes = right / "docs" / "notes"
    notes.mkdir()
    (notes / "inner.txt").write_bytes(b"dir")
    user = _config(left).tasks[0]
    project = _config(left).tasks[1]
    before = compute_benchmark_fingerprint(
        _config(left, tasks=[user, project, _extra_task(left, "kept")])
    )
    after_tasks = [
        project.model_copy(
            update={
                "prompt": "Different prompt.",
                "verify": project.verify.model_copy(update={"command": "python3 other.py"}),
            }
        ),
        user,
        _extra_task(right, "fresh"),
    ]
    after = compute_benchmark_fingerprint(_config(right, tasks=after_tasks))
    manifest = compare_benchmark_fingerprints(before, after)

    assert manifest.overall_equal is False
    assert manifest.docs.added_files == ("added.md", "notes/inner.txt")
    assert manifest.docs.modified_files == ("users.md",)
    assert manifest.docs.type_changes[0].path == "notes"
    assert manifest.docs.type_changes[0].before == "file"
    assert manifest.docs.type_changes[0].after == "directory"
    assert manifest.starter.removed_files == ("main.py",)
    assert manifest.tasks.added_task_ids == ("fresh",)
    assert manifest.tasks.removed_task_ids == ("kept",)
    assert manifest.tasks.reordered is True
    assert manifest.tasks.prompt_changed == ("create_project",)
    assert manifest.tasks.verifier_command_changed == ("create_project",)
    same = compare_benchmark_fingerprints(before, before)
    assert same.overall_equal is True
    assert same.docs.empty
    assert same.tasks.empty
    assert same.verifiers == ()


def test_malformed_manifest_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(BenchmarkFingerprintError, match="not valid JSON"):
        load_benchmark_manifest(path)

    missing = tmp_path / "missing.json"
    with pytest.raises(BenchmarkFingerprintError, match="not found"):
        load_benchmark_manifest(missing)


def _extra_task(root: Path, task_id: str) -> TaskConfig:
    path = root / "verifiers" / task_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "check.py").write_bytes(b"raise SystemExit(0)\n")
    return TaskConfig(
        id=task_id,
        prompt=f"Do {task_id}.",
        verify=VerifyConfig(path=path, command="python3 check.py"),
    )
