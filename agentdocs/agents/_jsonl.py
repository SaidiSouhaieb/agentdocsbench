"""Parse NDJSON stdout from a coding-agent CLI."""

import json
from typing import Any

from agentdocs.agents.base import AgentOutputError

_JSON_PREVIEW_LENGTH = 80


def parse_jsonl_objects(stdout: str, *, provider: str) -> tuple[dict[str, Any], ...]:
    """Return one object per nonblank JSONL line.

    A nonblank line that is not a JSON object raises ``AgentOutputError``.
    The message names ``provider`` and the 1-based line number.
    """
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentOutputError(
                f"{provider} returned invalid JSON on line {line_number}: "
                f"{_preview(line)}"
            ) from exc
        if not isinstance(value, dict):
            raise AgentOutputError(
                f"{provider} returned a non-object JSON value on line {line_number}."
            )
        events.append(value)
    return tuple(events)


def _preview(line: str) -> str:
    compact = " ".join(line.split())
    if len(compact) <= _JSON_PREVIEW_LENGTH:
        return compact
    return compact[: _JSON_PREVIEW_LENGTH - 3] + "..."
