"""Check app.users.create_user uses the current service."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusClient, NimbusConfig  # noqa: E402
from app.users import create_user  # noqa: E402

class _SpyUsers:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("User", (), {"id": "user-1", "name": kwargs["name"], "email": kwargs["email"], "metadata": dict(kwargs.get("metadata") or {})})()

class _Spy:
    def __init__(self) -> None:
        self.users = _SpyUsers()
        self.deprecated = 0

    def create_user(self, *args, **kwargs):
        self.deprecated += 1
        raise AssertionError("deprecated create_user was called")

client = _Spy()
result = create_user(client, name="Ada", email="ada@nimbus.local", metadata={"team": "platform"})
if client.deprecated:
    print("create_user called the deprecated client.create_user", file=sys.stderr)
    raise SystemExit(1)
if client.users.calls != [{"name": "Ada", "email": "ada@nimbus.local", "metadata": {"team": "platform"}}] and client.users.calls != [{"name": "Ada", "email": "ada@nimbus.local", "metadata": {"team": "platform"}, "idempotency_key": None}]:
    print(f"Unexpected create arguments {client.users.calls}", file=sys.stderr)
    raise SystemExit(1)
expected = {"id": "user-1", "name": "Ada", "email": "ada@nimbus.local", "metadata": {"team": "platform"}}
if result != expected:
    print(f"Expected {expected}, found {result}", file=sys.stderr)
    raise SystemExit(1)

real = NimbusClient(NimbusConfig(api_key="nim_test"))
created = create_user(real, name="Grace", email="grace@nimbus.local", metadata={})
if created["id"] != "user-1" or real.users.get(created["id"]).email != "grace@nimbus.local":
    print(f"Real client did not store the user: {created}", file=sys.stderr)
    raise SystemExit(1)
