"""Shared SHA-256 helpers for benchmark fingerprints and workspace snapshots.

File bytes are hashed in 1 MiB chunks. The canonical JSON form is UTF-8 with
sorted keys and no extra whitespace. Callers decide which trees are legal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK_BYTES = 1024 * 1024


def hash_file(path: Path) -> tuple[str, int]:
    """Return the SHA-256 hex digest and size of a regular file.

    The caller must already know ``path`` is a regular file. This function
    does not follow a symlink check of its own; opening a symlink would read
    the target, so callers reject or classify links first.
    """
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(value: object) -> str:
    """Hash the canonical JSON form of ``value``.

    Object keys are sorted. List order is preserved, so callers sort lists
    whose order is not meaningful before calling this.
    """
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
