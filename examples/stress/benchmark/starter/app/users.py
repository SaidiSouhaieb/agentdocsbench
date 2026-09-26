"""User application helpers. See docs/users.md and docs/pagination.md."""


def create_user(client, *, name: str, email: str, metadata: dict | None = None) -> dict:
    raise NotImplementedError("Create a user with client.users.create.")


def create_user_idempotent(client, *, name: str, email: str, metadata: dict | None, idempotency_key: str) -> dict:
    raise NotImplementedError("Create a user with an idempotency key.")


def list_all_users(client) -> list:
    raise NotImplementedError("Follow next_cursor until every user is returned.")
