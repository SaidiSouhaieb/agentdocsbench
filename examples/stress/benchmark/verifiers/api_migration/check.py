"""Check provision_user behavior and that it no longer calls create_user."""

import ast
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusClient, NimbusConfig  # noqa: E402
from app.legacy import provision_user  # noqa: E402

source = (project / "app" / "legacy.py").read_text(encoding="utf-8")
tree = ast.parse(source)
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "provision_user":
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr == "create_user":
                print("provision_user still references create_user", file=sys.stderr)
                raise SystemExit(1)

client = NimbusClient(NimbusConfig(api_key="nim_test"))
result = provision_user(client, name="Ada", email="ada@nimbus.local", metadata={"team": "platform"})
if result != {"id": "user-1", "name": "Ada", "email": "ada@nimbus.local", "metadata": {"team": "platform"}}:
    print(f"Unexpected provision result {result}", file=sys.stderr)
    raise SystemExit(1)
if client.users.get("user-1").email != "ada@nimbus.local":
    print("User was not stored", file=sys.stderr)
    raise SystemExit(1)
