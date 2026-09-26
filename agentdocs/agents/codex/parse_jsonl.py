"""Parse Codex JSONL stdout into event objects."""

from typing import Any

from agentdocs.agents._jsonl import parse_jsonl_objects


def _parse_jsonl(stdout: str) -> tuple[dict[str, Any], ...]:
    return parse_jsonl_objects(stdout, provider="Codex")
