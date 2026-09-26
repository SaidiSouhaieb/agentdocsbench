"""Apply a per-run model override without editing the config file."""

from agentdocs.config.agentdocs_config import AgentDocsConfig


def with_agent_model(config: AgentDocsConfig, model: str) -> AgentDocsConfig:
    """Return a copy of ``config`` whose agent model is ``model``.

    The original config object is left unchanged. A blank model is rejected.
    """
    stripped = model.strip()
    if not stripped:
        raise ValueError("Model must not be empty.")
    agent = config.agent.model_copy(update={"model": stripped})
    return config.model_copy(update={"agent": agent})
