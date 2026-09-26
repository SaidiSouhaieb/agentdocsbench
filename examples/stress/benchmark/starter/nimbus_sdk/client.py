"""Nimbus client. Prefer the service objects over deprecated methods."""

from nimbus_sdk.config import NimbusConfig
from nimbus_sdk.organizations import OrganizationService
from nimbus_sdk.users import UserService


class NimbusClient:
    def __init__(self, config: NimbusConfig) -> None:
        if not isinstance(config, NimbusConfig):
            raise TypeError("NimbusClient requires a NimbusConfig.")
        if not config.api_key:
            raise ValueError("api_key is required.")
        self.config = config
        self.user_seq = 0
        self.org_seq = 0
        self.users_by_id: dict = {}
        self.user_order: list[str] = []
        self.orgs_by_id: dict = {}
        self.memberships: list = []
        self.idempotency: dict = {}
        self.users = UserService(self)
        self.organizations = OrganizationService(self)

    def create_user(self, name: str, email: str, metadata: dict | None = None):
        """Deprecated since API version 2024-06-01. Use ``client.users.create``."""
        return self.users.create(name=name, email=email, metadata=metadata)
