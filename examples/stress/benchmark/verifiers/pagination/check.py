"""Check that every seeded page is returned."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusClient, NimbusConfig, User  # noqa: E402
from app.users import list_all_users  # noqa: E402

users = [
    User(id=f"user-{index}", name=f"User {index}", email=f"user{index}@nimbus.local", metadata={"n": str(index)})
    for index in range(1, 6)
]
client = NimbusClient(NimbusConfig(api_key="nim_test"))
client.users.seed(users)
found = list_all_users(client)
expected = [
    {"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
    for user in users
]
if found != expected:
    print(f"Expected {expected}, found {found}", file=sys.stderr)
    raise SystemExit(1)
