"""Find symbolic links without following them."""

from __future__ import annotations

import os
from pathlib import Path


def find_symlink(source: Path) -> Path | None:
    """Return the first symbolic link at or under ``source``.

    Directory links are reported and not entered.
    """
    if source.is_symlink():
        return source
    for directory, dir_names, file_names in os.walk(source, followlinks=False):
        parent = Path(directory)
        for name in (*dir_names, *file_names):
            candidate = parent / name
            if candidate.is_symlink():
                return candidate
    return None
