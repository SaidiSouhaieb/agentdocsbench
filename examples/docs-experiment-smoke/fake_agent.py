#!/usr/bin/env python3
"""Fake Cursor ``agent`` for the documentation-experiment smoke image.

It reads ``/workspace/docs/instruction.txt`` and writes the requested token
into the project. It does not use the network or provider credentials.
If the verifier tree is visible, or the docs mount is writable, it leaves an
error marker for the verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path("/workspace/project")
DOCS = Path("/workspace/docs")
VERIFIER = Path("/workspace/verifier")
INSTRUCTION = DOCS / "instruction.txt"


def main() -> int:
    if "--version" in sys.argv[1:]:
        print("agentdocs-docs-experiment-smoke")
        return 0
    errors: list[str] = []
    if VERIFIER.exists():
        errors.append("verifier-visible")
    probe = DOCS / ".agentdocs-ro-probe"
    try:
        probe.write_text("writable\n", encoding="utf-8")
    except OSError:
        pass
    else:
        errors.append("docs-writable")
        try:
            probe.unlink()
        except OSError:
            pass
    PROJECT.mkdir(parents=True, exist_ok=True)
    if errors:
        (PROJECT / "isolation-error.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
    token = _instruction_token()
    (PROJECT / "result.txt").write_text(token + "\n", encoding="utf-8")
    print('{"type":"result","subtype":"success"}')
    return 0


def _instruction_token() -> str:
    if not INSTRUCTION.is_file():
        return "MISSING_INSTRUCTION"
    for line in INSTRUCTION.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("WRITE "):
            return text.removeprefix("WRITE ").strip()
    return "UNREADABLE_INSTRUCTION"


if __name__ == "__main__":
    raise SystemExit(main())
