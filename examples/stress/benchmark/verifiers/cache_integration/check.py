"""Check cache miss, hit, and expiry."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import MemoryCache, NimbusClient, NimbusConfig  # noqa: E402
from app.cache import get_user  # noqa: E402

clock = {"now": 0.0}
cache = MemoryCache(clock=lambda: clock["now"])
client = NimbusClient(NimbusConfig(api_key="nim_test"))
created = client.users.create(name="Ada", email="ada@nimbus.local", metadata={"team": "platform"})
reads = {"n": 0}
original = client.users.get

def counting_get(user_id):
    reads["n"] += 1
    return original(user_id)

client.users.get = counting_get
first = get_user(client, cache, created.id)
second = get_user(client, cache, created.id)
if reads["n"] != 1 or first != second or first["email"] != "ada@nimbus.local":
    print(f"Cache hit failed reads={reads['n']} first={first}", file=sys.stderr)
    raise SystemExit(1)
clock["now"] = 60.0
third = get_user(client, cache, created.id)
if reads["n"] != 2 or third["id"] != created.id:
    print(f"Expiry failed reads={reads['n']} third={third}", file=sys.stderr)
    raise SystemExit(1)
cache.invalidate(f"user:{created.id}")
get_user(client, cache, created.id)
if reads["n"] != 3:
    print(f"Invalidate did not miss: reads={reads['n']}", file=sys.stderr)
    raise SystemExit(1)
