"""Copy a benchmark config onto one matrix target."""

from agentdocs.config.agent_config import AgentConfig
from agentdocs.config.agentdocs_config import AgentDocsConfig


def with_agent(
    config: AgentDocsConfig,
    *,
    agent_type: str,
    model: str | None,
) -> AgentDocsConfig:
    """Return a copy of ``config`` with a replaced agent type and model.

    Docs, starter, tasks, and verifiers stay as they are. The original config
    object is left unchanged. ``model`` may be ``None``, which means the
    provider CLI default.
    """
    agent = AgentConfig(type=agent_type, model=model)
    return config.model_copy(update={"agent": agent})
