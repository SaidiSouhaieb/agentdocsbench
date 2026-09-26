"""Check idempotency keys are honored."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusClient, NimbusConfig  # noqa: E402
from app.users import create_user_idempotent  # noqa: E402

client = NimbusClient(NimbusConfig(api_key="nim_test"))
first = create_user_idempotent(
    client,
    name="Ada",
    email="ada@nimbus.local",
    metadata={"team": "platform"},
    idempotency_key="key-1",
)
second = create_user_idempotent(
    client,
    name="Different",
    email="other@nimbus.local",
    metadata={"team": "other"},
    idempotency_key="key-1",
)
third = create_user_idempotent(
    client,
    name="Grace",
    email="grace@nimbus.local",
    metadata={},
    idempotency_key="key-2",
)
if first != second or first["id"] == third["id"]:
    print(f"Idempotency failed: {first} {second} {third}", file=sys.stderr)
    raise SystemExit(1)
if len(client.users_by_id) != 2:
    print(f"Expected 2 stored users, found {len(client.users_by_id)}", file=sys.stderr)
    raise SystemExit(1)
if first["metadata"] != {"team": "platform"} or third["email"] != "grace@nimbus.local":
    print(f"Result shape mismatch {first} {third}", file=sys.stderr)
    raise SystemExit(1)
