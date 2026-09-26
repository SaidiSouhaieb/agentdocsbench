"""Read an AgentDocsBench YAML file as a mapping."""

from pathlib import Path
from typing import Any

import yaml

from agentdocs.config.errors import ConfigError


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Config file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file must contain a YAML mapping, not {type(data).__name__}: {path}"
        )
    return data
