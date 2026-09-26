"""Escape text that may be placed in a Markdown table or job summary.

Task ids, model ids, target ids, and image names can be chosen by the
benchmark author. Run summaries and GitHub Actions summaries share these
rules so the two presentations cannot drift.
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def markdown_table_cell(value: str) -> str:
    """Return one cell that cannot break a Markdown table row.

    Backslashes and pipes are escaped. Newlines, tabs, and other control
    characters become spaces. Backticks become apostrophes so a value cannot
    open a code span. Angle brackets are written as HTML entities.
    """
    text = _CONTROL.sub(" ", value)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("`", "'")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text
