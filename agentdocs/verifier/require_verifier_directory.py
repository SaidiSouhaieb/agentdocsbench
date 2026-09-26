"""Check that a verifier source path is an existing directory."""

from pathlib import Path


def _require_verifier_directory(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Verifier directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Verifier path is not a directory: {path}")
