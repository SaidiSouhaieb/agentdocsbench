"""Check organization membership."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusClient, NimbusConfig  # noqa: E402
from app.organizations import add_user_to_organization  # noqa: E402

client = NimbusClient(NimbusConfig(api_key="nim_test"))
result = add_user_to_organization(
    client,
    org_name="Nimbus",
    user_name="Ada",
    user_email="ada@nimbus.local",
    role="admin",
)
if result["role"] != "admin":
    print(f"Expected admin, found {result}", file=sys.stderr)
    raise SystemExit(1)
if result["organization_id"] not in client.orgs_by_id:
    print("Organization was not stored", file=sys.stderr)
    raise SystemExit(1)
if result["user_id"] not in client.users_by_id:
    print("User was not stored", file=sys.stderr)
    raise SystemExit(1)
membership = client.memberships[-1]
if (membership.organization_id, membership.user_id, membership.role) != (
    result["organization_id"],
    result["user_id"],
    "admin",
):
    print(f"Membership mismatch {membership}", file=sys.stderr)
    raise SystemExit(1)
