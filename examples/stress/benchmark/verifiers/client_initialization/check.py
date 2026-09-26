"""Check app.client.build_client."""

import json
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from app.client import build_client  # noqa: E402

config_path = project / "config" / "app.json"
client = build_client(config_path)
expected = json.loads(config_path.read_text(encoding="utf-8"))
actual = {
    "api_key": client.config.api_key,
    "base_url": client.config.base_url,
    "timeout_seconds": client.config.timeout_seconds,
    "api_version": client.config.api_version,
}
if actual != expected:
    print(f"Expected {expected}, found {actual}", file=sys.stderr)
    raise SystemExit(1)
