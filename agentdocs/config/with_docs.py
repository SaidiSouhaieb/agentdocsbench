"""Copy a benchmark config onto one documentation variant."""

from pathlib import Path

from agentdocs.config.agentdocs_config import AgentDocsConfig


def with_docs(config: AgentDocsConfig, docs: Path) -> AgentDocsConfig:
    """Return a copy of ``config`` whose docs path is ``docs``.

    Starter, agent, model, tasks, prompts, and verifiers stay as they are.
    The original config object is left unchanged.
    """
    return config.model_copy(update={"docs": docs})
