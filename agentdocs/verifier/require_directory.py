"""Check that the agent project path is an existing directory."""

from pathlib import Path


def _require_directory(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label} directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{label} path is not a directory: {path}")
    return path.resolve()
