"""Resolve a config path against the YAML file's directory."""

from pathlib import Path


def _resolve_against_config_dir(base_dir: Path, path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()
