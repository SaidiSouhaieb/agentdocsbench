"""Succeed only when the agent project contains a user named Alice."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
user_file = project / "user.txt"

if not user_file.is_file():
    print(f"Missing {user_file}", file=sys.stderr)
    raise SystemExit(1)

content = user_file.read_text(encoding="utf-8").strip()
if content != "Alice":
    print(f"Expected Alice, found {content!r}", file=sys.stderr)
    raise SystemExit(1)
