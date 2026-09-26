"""Expect the candidate documentation token, and a private verifier container."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
docs = Path("/workspace/docs")

if docs.exists():
    print("docs tree is visible inside the verifier container", file=sys.stderr)
    raise SystemExit(1)

marker = project / "isolation-error.txt"
if marker.exists():
    print(marker.read_text(encoding="utf-8"), file=sys.stderr)
    raise SystemExit(1)

result = project / "result.txt"
if not result.is_file():
    print("missing result.txt", file=sys.stderr)
    raise SystemExit(1)
if result.read_text(encoding="utf-8").strip() != "EXPECTED_RESULT":
    print("result.txt did not contain EXPECTED_RESULT", file=sys.stderr)
    raise SystemExit(1)
print("docs experiment smoke ok")
