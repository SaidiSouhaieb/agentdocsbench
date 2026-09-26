"""Provider failure classification. No provider CLI is launched."""

from __future__ import annotations

from agentdocs.agents.failures import (
    FAILURE_SCHEMA_VERSION,
    AgentFailureKind,
    classify_agent_failure,
    failure_payload,
)

_OBSERVED_CODEX_USAGE_LIMIT = (
    "You\u2019ve hit your usage limit. Upgrade to Pro "
    "(https://chatgpt.com/explore/pro), visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits "
    "or try again at 8:35 PM."
)
_OBSERVED_CLAUDE_CREDIT = "Credit balance is too low"


def _classify(
    *,
    provider: str = "codex",
    exit_code: int = 1,
    events: tuple[dict[str, object], ...] = (),
    stdout: str = "",
    stderr: str = "",
):
    return classify_agent_failure(
        provider=provider,
        exit_code=exit_code,
        events=events,
        stdout=stdout,
        stderr=stderr,
    )


def test_exit_zero_has_no_failure() -> None:
    assert _classify(exit_code=0, stderr="warning: something failed") is None


def test_unmatched_nonzero_is_unknown_and_non_blocking() -> None:
    failure = _classify(stderr="the command failed")

    assert failure is not None
    assert failure.kind is AgentFailureKind.unknown_agent_failure
    assert failure.blocking is False
    assert failure.rule_id == "unmatched_nonzero_exit"
    assert failure.source == "exit_code"
    assert failure.schema_version == FAILURE_SCHEMA_VERSION


def test_observed_claude_credit_balance_is_quota() -> None:
    failure = _classify(
        provider="claude",
        stderr=f"Error: {_OBSERVED_CLAUDE_CREDIT}\n",
    )

    assert failure is not None
    assert failure.kind is AgentFailureKind.quota_or_credit
    assert failure.blocking is True
    assert failure.rule_id == "claude.credit_balance_low"
    assert failure.source == "stderr"


def test_claude_credit_rule_is_case_insensitive() -> None:
    failure = _classify(provider="claude", stderr="CREDIT BALANCE IS TOO LOW")

    assert failure is not None
    assert failure.rule_id == "claude.credit_balance_low"


def test_observed_codex_usage_limit_event_is_quota() -> None:
    events = (
        {"type": "thread.started", "thread_id": "abc"},
        {"type": "error", "message": _OBSERVED_CODEX_USAGE_LIMIT},
        {"type": "turn.failed", "error": {"message": _OBSERVED_CODEX_USAGE_LIMIT}},
    )

    failure = _classify(provider="codex", events=events, stdout="ignored")

    assert failure is not None
    assert failure.kind is AgentFailureKind.quota_or_credit
    assert failure.blocking is True
    assert failure.rule_id == "codex.usage_limit"
    assert failure.source == "structured_event"


def test_synthetic_rules_cover_the_remaining_blocking_kinds() -> None:
    cases = (
        ("agentdocsbench synthetic authentication required", "authentication", "synthetic.authentication"),
        ("agentdocsbench synthetic invalid model", "invalid_model", "synthetic.invalid_model"),
        ("agentdocsbench synthetic rate limit exceeded", "rate_limit", "synthetic.rate_limit"),
        (
            "agentdocsbench synthetic provider unavailable",
            "provider_unavailable",
            "synthetic.provider_unavailable",
        ),
    )
    for stderr, kind, rule_id in cases:
        failure = _classify(stderr=stderr.upper())
        assert failure is not None
        assert failure.kind.value == kind
        assert failure.blocking is True
        assert failure.rule_id == rule_id
        assert failure.source == "stderr"


def test_vague_text_and_bare_status_codes_stay_unknown() -> None:
    failure = _classify(
        stderr="error failed limit model auth network 401 429",
        stdout="rate limit exceeded",
    )

    assert failure is not None
    assert failure.kind is AgentFailureKind.unknown_agent_failure


def test_exit_zero_ignores_a_known_stderr_phrase() -> None:
    assert _classify(exit_code=0, provider="claude", stderr=_OBSERVED_CLAUDE_CREDIT) is None


def test_model_text_containing_provider_phrases_does_not_classify() -> None:
    events = (
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": (
                    "Implement rate limiting. "
                    + _OBSERVED_CODEX_USAGE_LIMIT
                    + " "
                    + _OBSERVED_CLAUDE_CREDIT
                ),
            },
        },
    )

    failure = _classify(provider="codex", events=events, stdout=_OBSERVED_CODEX_USAGE_LIMIT)

    assert failure is not None
    assert failure.kind is AgentFailureKind.unknown_agent_failure


def test_nested_event_payloads_are_not_searched() -> None:
    events = (
        {
            "type": "error",
            "details": {"message": _OBSERVED_CODEX_USAGE_LIMIT},
            "message": {"content": [{"text": _OBSERVED_CODEX_USAGE_LIMIT}]},
        },
    )

    failure = _classify(events=events)

    assert failure is not None
    assert failure.kind is AgentFailureKind.unknown_agent_failure


def test_turn_failed_error_message_is_a_structured_codex_field() -> None:
    events = ({"type": "turn.failed", "error": {"message": _OBSERVED_CODEX_USAGE_LIMIT}},)

    failure = _classify(events=events)

    assert failure is not None
    assert failure.rule_id == "codex.usage_limit"


def test_precedence_prefers_authentication_over_rate_limit() -> None:
    failure = _classify(
        stderr=(
            "agentdocsbench synthetic rate limit exceeded\n"
            "agentdocsbench synthetic authentication required\n"
        )
    )

    assert failure is not None
    assert failure.kind is AgentFailureKind.authentication
    assert failure.rule_id == "synthetic.authentication"


def test_rule_id_is_stable_and_matched_text_is_not_stored() -> None:
    failure = _classify(provider="claude", stderr=_OBSERVED_CLAUDE_CREDIT)
    payload = failure_payload(failure)

    assert failure is not None
    assert payload == {
        "schema_version": 1,
        "kind": "quota_or_credit",
        "blocking": True,
        "rule_id": "claude.credit_balance_low",
        "source": "stderr",
    }
    assert _OBSERVED_CLAUDE_CREDIT not in str(payload)


def test_provider_rules_do_not_cross_providers() -> None:
    claude_on_codex = _classify(provider="codex", stderr=_OBSERVED_CLAUDE_CREDIT)
    codex_event_on_claude = _classify(
        provider="claude",
        events=({"type": "error", "message": _OBSERVED_CODEX_USAGE_LIMIT},),
    )
    synthetic_on_cursor = _classify(
        provider="cursor",
        stderr="agentdocsbench synthetic authentication required",
    )

    assert claude_on_codex is not None
    assert claude_on_codex.kind is AgentFailureKind.unknown_agent_failure
    assert codex_event_on_claude is not None
    assert codex_event_on_claude.kind is AgentFailureKind.unknown_agent_failure
    assert synthetic_on_cursor is not None
    assert synthetic_on_cursor.kind is AgentFailureKind.unknown_agent_failure


def test_exit_code_alone_does_not_select_a_known_kind() -> None:
    for code in (1, 2, 401, 429):
        failure = _classify(exit_code=code)
        assert failure is not None
        assert failure.kind is AgentFailureKind.unknown_agent_failure
        assert failure.source == "exit_code"
