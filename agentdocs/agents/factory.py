"""Choose a coding-agent adapter from a config agent type."""

from agentdocs.agents.base import AgentAdapter
from agentdocs.agents.claude import ClaudeAdapter
from agentdocs.agents.codex import CodexAdapter
from agentdocs.agents.cursor import CursorAdapter
from agentdocs.config.constants import SUPPORTED_AGENT_TYPES
from agentdocs.execution.base import ExecutionRuntime


def create_agent_adapter(
    agent_type: str,
    *,
    model: str | None = None,
    runtime: ExecutionRuntime | None = None,
) -> AgentAdapter:
    """Return the adapter for ``agent_type``.

    ``model`` is forwarded as the requested model. ``None`` leaves the
    provider CLI default unchanged. ``runtime`` selects host or Docker
    execution. ``None`` uses the local runtime. This is a runtime check.
    Config loading already rejects unsupported types.
    """
    if agent_type == "codex":
        return CodexAdapter(model=model, runtime=runtime)
    if agent_type == "claude":
        return ClaudeAdapter(model=model, runtime=runtime)
    if agent_type == "cursor":
        return CursorAdapter(model=model, runtime=runtime)
    supported = ", ".join(sorted(SUPPORTED_AGENT_TYPES))
    raise ValueError(
        f"Unsupported agent type {agent_type!r}. Supported agent types: {supported}."
    )
