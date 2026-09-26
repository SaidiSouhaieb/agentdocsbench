"""Check the smoke project and the verifier container boundary."""

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
if result.read_text(encoding="utf-8").strip() != "DOCKER_OK":
    print("result.txt did not contain DOCKER_OK", file=sys.stderr)
    raise SystemExit(1)
print("docker smoke ok")
