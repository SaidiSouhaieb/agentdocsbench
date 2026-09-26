"""Deprecated user provisioning. See docs/migrations.md."""


def provision_user(client, *, name: str, email: str, metadata: dict | None = None) -> dict:
    user = client.create_user(name, email, metadata)
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "metadata": dict(user.metadata),
    }
