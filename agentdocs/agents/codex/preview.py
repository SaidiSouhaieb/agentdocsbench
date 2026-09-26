"""Shorten a bad JSONL line for an error message."""

_JSON_PREVIEW_LENGTH = 80


def _preview(line: str) -> str:
    compact = " ".join(line.split())
    if len(compact) <= _JSON_PREVIEW_LENGTH:
        return compact
    return compact[: _JSON_PREVIEW_LENGTH - 3] + "..."
