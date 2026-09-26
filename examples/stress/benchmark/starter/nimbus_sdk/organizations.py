"""Organizations and memberships stored on one NimbusClient."""

from nimbus_sdk.errors import NimbusNotFoundError, NimbusValidationError
from nimbus_sdk.models import Membership, Organization

_ROLES = frozenset({"owner", "admin", "member"})


class OrganizationService:
    def __init__(self, client) -> None:
        self._client = client

    def create(self, *, name: str) -> Organization:
        if not name:
            raise NimbusValidationError("Organization name is required.")
        self._client.org_seq += 1
        org = Organization(id=f"org-{self._client.org_seq}", name=name)
        self._client.orgs_by_id[org.id] = org
        return org

    def add_member(self, organization_id: str, *, user_id: str, role: str) -> Membership:
        if organization_id not in self._client.orgs_by_id:
            raise NimbusNotFoundError(f"Organization {organization_id!r} was not found.")
        if user_id not in self._client.users_by_id:
            raise NimbusNotFoundError(f"User {user_id!r} was not found.")
        if role not in _ROLES:
            raise NimbusValidationError(f"Unsupported role {role!r}.")
        membership = Membership(organization_id=organization_id, user_id=user_id, role=role)
        self._client.memberships.append(membership)
        return membership
