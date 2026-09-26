#!/usr/bin/env python3
"""Fake Cursor ``agent`` for the Docker smoke image.

It writes a known project file and valid JSONL. It does not use the network.
If the verifier tree is visible, or the docs mount is writable, it leaves an
error marker for the verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path("/workspace/project")
DOCS = Path("/workspace/docs")
VERIFIER = Path("/workspace/verifier")


def main() -> int:
    if "--version" in sys.argv[1:]:
        print("agentdocs-docker-smoke")
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
    (PROJECT / "result.txt").write_text("DOCKER_OK\n", encoding="utf-8")
    print('{"type":"result","subtype":"success"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
