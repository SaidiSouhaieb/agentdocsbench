"""Agent selection in an AgentDocsBench config file."""

from pydantic import BaseModel, ConfigDict, field_validator

from agentdocs.config.constants import SUPPORTED_AGENT_TYPES


class AgentConfig(BaseModel):
    """Coding agent selected to run the benchmark tasks.

    ``model`` is the model id requested for this run. ``None`` means the
    provider CLI keeps its own default. AgentDocsBench does not check the id
    against a catalog.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    model: str | None = None

    @field_validator("type")
    @classmethod
    def type_must_be_supported(cls, agent_type: str) -> str:
        normalized = agent_type.strip()
        if normalized not in SUPPORTED_AGENT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_AGENT_TYPES))
            raise ValueError(
                f"Unsupported agent type {normalized!r}. "
                f"Supported agent types: {supported}."
            )
        return normalized

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, model: str | None) -> str | None:
        if model is None:
            return None
        stripped = model.strip()
        if not stripped:
            raise ValueError("Model must not be empty.")
        return stripped
