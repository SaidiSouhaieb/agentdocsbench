"""One documentation-driven programming task."""

from pydantic import BaseModel, ConfigDict, field_validator

from agentdocs.config.require_text import _require_text
from agentdocs.config.verify_config import VerifyConfig


class TaskConfig(BaseModel):
    """One documentation-driven programming task."""

    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    verify: VerifyConfig

    @field_validator("id")
    @classmethod
    def id_must_not_be_empty(cls, task_id: str) -> str:
        return _require_text(task_id, "Task id must not be empty.")

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_empty(cls, prompt: str) -> str:
        return _require_text(prompt, "Task prompt must not be empty.")
