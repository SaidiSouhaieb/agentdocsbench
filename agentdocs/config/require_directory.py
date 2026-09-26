"""Check that a config path is an existing directory."""

from pathlib import Path


def _require_directory(label: str, path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{label} path is not a directory: {path}")
