"""Reject a blank Codex task prompt."""


def _require_prompt(prompt: str) -> str:
    stripped = prompt.strip()
    if not stripped:
        raise ValueError("Task prompt must not be empty.")
    return stripped
