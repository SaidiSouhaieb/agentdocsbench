"""Check the audit plugin is registered and notified."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import User  # noqa: E402
from app.plugins import build_registry  # noqa: E402

registry = build_registry()
plugin = registry.get("audit")
user = User(id="user-7", name="Ada", email="ada@nimbus.local", metadata={})
registry.notify_user_created(user)
if plugin.seen != ["user-7"]:
    print(f"Plugin did not record the user: {plugin.seen}", file=sys.stderr)
    raise SystemExit(1)
if plugin.name != "audit":
    print(f"Unexpected plugin name {plugin.name}", file=sys.stderr)
    raise SystemExit(1)
