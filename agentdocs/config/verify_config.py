"""Verifier settings for one task."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from agentdocs.config.require_text import _require_text


class VerifyConfig(BaseModel):
    """Verifier directory and command used to check whether a task succeeded."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    command: str

    @field_validator("command")
    @classmethod
    def command_must_not_be_empty(cls, command: str) -> str:
        return _require_text(command, "Verifier command must not be empty.")
