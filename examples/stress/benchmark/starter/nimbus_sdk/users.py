"""User records stored on one NimbusClient."""

from nimbus_sdk.errors import NimbusNotFoundError, NimbusValidationError
from nimbus_sdk.models import Page, User


class UserService:
    def __init__(self, client) -> None:
        self._client = client

    def create(self, *, name: str, email: str, metadata: dict | None = None, idempotency_key: str | None = None) -> User:
        if not name or not email or "@" not in email:
            raise NimbusValidationError("name and a valid email are required.")
        stored = dict(metadata or {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in stored.items()):
            raise NimbusValidationError("metadata keys and values must be strings.")
        if idempotency_key:
            existing = self._client.idempotency.get(idempotency_key)
            if existing is not None:
                return existing
        self._client.user_seq += 1
        user = User(
            id=f"user-{self._client.user_seq}",
            name=name,
            email=email,
            metadata=stored,
        )
        self._client.users_by_id[user.id] = user
        self._client.user_order.append(user.id)
        if idempotency_key:
            self._client.idempotency[idempotency_key] = user
        return user

    def get(self, user_id: str) -> User:
        user = self._client.users_by_id.get(user_id)
        if user is None:
            raise NimbusNotFoundError(f"User {user_id!r} was not found.")
        return user

    def seed(self, users: list[User]) -> None:
        """Replace the local directory. Ids must already be assigned."""
        self._client.users_by_id = {}
        self._client.user_order = []
        for user in users:
            self._client.users_by_id[user.id] = user
            self._client.user_order.append(user.id)
        self._client.user_seq = len(users)

    def list(self, *, cursor: str | None = None, page_size: int = 2) -> Page:
        if page_size < 1:
            raise NimbusValidationError("page_size must be at least 1.")
        order = self._client.user_order or list(self._client.users_by_id)
        start = 0
        if cursor is not None:
            if not cursor.startswith("offset:"):
                raise NimbusValidationError(f"Invalid cursor {cursor!r}.")
            start = int(cursor.split(":", 1)[1])
        chunk = order[start : start + page_size]
        next_index = start + page_size
        next_cursor = f"offset:{next_index}" if next_index < len(order) else None
        return Page(
            items=tuple(self._client.users_by_id[user_id] for user_id in chunk),
            next_cursor=next_cursor,
        )
