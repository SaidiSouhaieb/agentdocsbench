"""Run the users list command against a verifier-owned directory."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
directory = Path(tempfile.gettempdir()) / "nimbus-directory.json"
users = [
    {"id": f"user-{index}", "name": f"User {index}", "email": f"u{index}@nimbus.local", "metadata": {}}
    for index in range(1, 6)
]
directory.write_text(json.dumps(users), encoding="utf-8")
completed = subprocess.run(
    [sys.executable, "-m", "app.cli", "users", "list", "--directory", str(directory)],
    cwd=project,
    capture_output=True,
    text=True,
    check=False,
)
expected = "".join(f"{user['id']} {user['email']}\n" for user in users)
if completed.returncode != 0 or completed.stdout != expected:
    print(completed.stdout, file=sys.stderr)
    print(completed.stderr, file=sys.stderr)
    print(f"Expected exit 0 and {expected!r}", file=sys.stderr)
    raise SystemExit(1)
