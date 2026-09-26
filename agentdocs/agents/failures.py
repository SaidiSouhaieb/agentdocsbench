"""Classify provider execution failures separately from verifier results.

Rules are provider-specific and conservative. A non-zero process exit with no
confident match is ``unknown_agent_failure`` and is not benchmark-blocking.
Exit 0 is never a failure, including when stderr contains a warning.

Stdout is accepted and ignored. Model text, prompts, and nested event payloads
are not scanned. The Codex rule reads only the top-level ``message`` of a
``type: error`` event and the ``error.message`` string of a ``type: turn.failed``
event. Those are the fields captured from a real Codex usage-limit run.

Synthetic rules use exact sentinel phrases. They exercise the taxonomy in
tests. They are not observed provider output.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

FAILURE_SCHEMA_VERSION = 1

_CODEX_USAGE_LIMIT = "hit your usage limit"
_CLAUDE_CREDIT_BALANCE = "credit balance is too low"


class AgentFailureKind(StrEnum):
    """Execution diagnostic. This does not judge model quality."""

    authentication = "authentication"
    quota_or_credit = "quota_or_credit"
    rate_limit = "rate_limit"
    invalid_model = "invalid_model"
    provider_unavailable = "provider_unavailable"
    unknown_agent_failure = "unknown_agent_failure"


BLOCKING_FAILURE_KINDS: tuple[AgentFailureKind, ...] = (
    AgentFailureKind.authentication,
    AgentFailureKind.invalid_model,
    AgentFailureKind.quota_or_credit,
    AgentFailureKind.rate_limit,
    AgentFailureKind.provider_unavailable,
)

_DISPLAY_LABELS = {
    AgentFailureKind.authentication: "authentication",
    AgentFailureKind.quota_or_credit: "quota/credit",
    AgentFailureKind.rate_limit: "rate limit",
    AgentFailureKind.invalid_model: "invalid model",
    AgentFailureKind.provider_unavailable: "provider unavailable",
    AgentFailureKind.unknown_agent_failure: "unknown agent failure",
}


@dataclass(frozen=True)
class AgentFailureClassification:
    """Why a returned agent process did not exit 0.

    ``rule_id`` names the rule that matched. The matched provider text stays
    in the existing stdout and stderr logs.
    """

    schema_version: int
    kind: AgentFailureKind
    blocking: bool
    rule_id: str
    source: str


@dataclass(frozen=True)
class _Rule:
    priority: int
    rule_id: str
    provider: str
    kind: AgentFailureKind
    source: str
    needle: str


# Lower priority wins. Equal priority keeps the earlier rule.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        10,
        "synthetic.authentication",
        "codex",
        AgentFailureKind.authentication,
        "stderr",
        "agentdocsbench synthetic authentication required",
    ),
    _Rule(
        20,
        "synthetic.invalid_model",
        "codex",
        AgentFailureKind.invalid_model,
        "stderr",
        "agentdocsbench synthetic invalid model",
    ),
    _Rule(
        30,
        "codex.usage_limit",
        "codex",
        AgentFailureKind.quota_or_credit,
        "structured_event",
        _CODEX_USAGE_LIMIT,
    ),
    _Rule(
        30,
        "claude.credit_balance_low",
        "claude",
        AgentFailureKind.quota_or_credit,
        "stderr",
        _CLAUDE_CREDIT_BALANCE,
    ),
    _Rule(
        40,
        "synthetic.rate_limit",
        "codex",
        AgentFailureKind.rate_limit,
        "stderr",
        "agentdocsbench synthetic rate limit exceeded",
    ),
    _Rule(
        50,
        "synthetic.provider_unavailable",
        "codex",
        AgentFailureKind.provider_unavailable,
        "stderr",
        "agentdocsbench synthetic provider unavailable",
    ),
)


def classify_agent_failure(
    *,
    provider: str,
    exit_code: int,
    events: tuple[dict[str, Any], ...],
    stdout: str,
    stderr: str,
) -> AgentFailureClassification | None:
    """Return a classification for a finished agent process.

    ``stdout`` is not a classification source. Callers still pass it so the
    adapter boundary stays explicit.
    """
    del stdout
    if exit_code == 0:
        return None
    matched = _first_match(provider, events, stderr)
    if matched is None:
        return AgentFailureClassification(
            schema_version=FAILURE_SCHEMA_VERSION,
            kind=AgentFailureKind.unknown_agent_failure,
            blocking=False,
            rule_id="unmatched_nonzero_exit",
            source="exit_code",
        )
    return AgentFailureClassification(
        schema_version=FAILURE_SCHEMA_VERSION,
        kind=matched.kind,
        blocking=True,
        rule_id=matched.rule_id,
        source=matched.source,
    )


def failure_display_label(kind: AgentFailureKind) -> str:
    """Short terminal label for one failure kind."""
    return _DISPLAY_LABELS[kind]


def failure_payload(failure: AgentFailureClassification | None) -> dict[str, object] | None:
    """Serialize classification metadata. Matched provider text is omitted."""
    if failure is None:
        return None
    return {
        "schema_version": failure.schema_version,
        "kind": failure.kind.value,
        "blocking": failure.blocking,
        "rule_id": failure.rule_id,
        "source": failure.source,
    }


def _first_match(
    provider: str,
    events: tuple[dict[str, Any], ...],
    stderr: str,
) -> _Rule | None:
    found: list[tuple[int, int, _Rule]] = []
    for index, rule in enumerate(_RULES):
        if _rule_matches(rule, provider, events, stderr):
            found.append((rule.priority, index, rule))
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1]))
    return found[0][2]


def _rule_matches(
    rule: _Rule,
    provider: str,
    events: tuple[dict[str, Any], ...],
    stderr: str,
) -> bool:
    if rule.provider != provider:
        return False
    needle = rule.needle.casefold()
    if rule.source == "stderr":
        return needle in stderr.casefold()
    if rule.source == "structured_event" and rule.provider == "codex":
        return any(needle in message.casefold() for message in _codex_error_messages(events))
    return False


def _codex_error_messages(events: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    """Read the two Codex error fields observed in captured JSONL.

    Other event types, nested objects, and assistant text are ignored.
    """
    messages: list[str] = []
    for event in events:
        event_type = event.get("type")
        if event_type == "error":
            message = event.get("message")
            if isinstance(message, str):
                messages.append(message)
            continue
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    messages.append(message)
    return tuple(messages)
