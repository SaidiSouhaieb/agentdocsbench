"""Check paged import, retries, auth failure, and cache invalidation."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import MemoryCache, NimbusAuthError, NimbusClient, NimbusConfig, NimbusServerError, Page, User  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.sync import import_organization_users  # noqa: E402

users = [
    User(id=f"user-{index}", name=f"User {index}", email=f"u{index}@nimbus.local", metadata={})
    for index in range(1, 5)
]

class Directory:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def list_page(self, *, cursor, page_size):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise NimbusServerError(503, "unavailable")
        start = 0 if cursor is None else int(cursor.split(":", 1)[1])
        chunk = users[start : start + page_size]
        next_index = start + page_size
        next_cursor = f"offset:{next_index}" if next_index < len(users) else None
        return Page(items=tuple(chunk), next_cursor=next_cursor)

client = NimbusClient(NimbusConfig(api_key="nim_test"))
for user in users:
    client.users_by_id[user.id] = user
cache = MemoryCache()
for user in users:
    cache.set(f"user:{user.id}", {"id": user.id}, ttl_seconds=60)
directory = Directory(fail_times=1)
result = import_organization_users(client, cache, org_name="Nimbus", directory=directory)
if result["imported_user_ids"] != [user.id for user in users] or result["member_count"] != 4:
    print(f"Import result mismatch {result}", file=sys.stderr)
    raise SystemExit(1)
if cache.get("user:user-1") is not None:
    print("Cache was not invalidated", file=sys.stderr)
    raise SystemExit(1)
roles = [item.role for item in client.memberships]
if roles != ["member", "member", "member", "member"]:
    print(f"Roles mismatch {roles}", file=sys.stderr)
    raise SystemExit(1)

class AuthDirectory:
    def list_page(self, *, cursor, page_size):
        raise NimbusAuthError("nope")

before = len(client.orgs_by_id)
try:
    import_organization_users(client, cache, org_name="Other", directory=AuthDirectory())
except AppError as exc:
    if exc.code != "auth_failed" or exc.status != 401:
        print(f"Auth import mapped wrong {exc.code} {exc.status}", file=sys.stderr)
        raise SystemExit(1)
else:
    print("Auth failure did not raise AppError", file=sys.stderr)
    raise SystemExit(1)
if len(client.orgs_by_id) != before:
    print("Organization was created after auth failure", file=sys.stderr)
    raise SystemExit(1)
