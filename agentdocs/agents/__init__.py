"""Coding-agent adapters."""

from agentdocs.agents.base import (
    AgentAdapter,
    AgentError,
    AgentExecutableNotFoundError,
    AgentOutputError,
    AgentRunResult,
    AgentTimeoutError,
)
from agentdocs.agents.claude import ClaudeAdapter
from agentdocs.agents.codex import CodexAdapter
from agentdocs.agents.cursor import CursorAdapter
from agentdocs.agents.factory import create_agent_adapter

__all__ = [
    "AgentAdapter",
    "AgentError",
    "AgentExecutableNotFoundError",
    "AgentOutputError",
    "AgentRunResult",
    "AgentTimeoutError",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "create_agent_adapter",
]
