"""Validate raw YAML data against the config models."""

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentdocs.config.agentdocs_config import AgentDocsConfig
from agentdocs.config.errors import ConfigError
from agentdocs.config.format_validation_error import _format_validation_error


def _validate_data(path: Path, data: dict[str, Any]) -> AgentDocsConfig:
    try:
        return AgentDocsConfig.model_validate(data)
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise ConfigError(f"Invalid configuration in {path}:\n{details}") from exc
