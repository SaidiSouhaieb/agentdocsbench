# Idempotency

`app.users.create_user_idempotent(client, *, name, email, metadata, idempotency_key)` creates a user through the current API described in `users.md`.

Pass `idempotency_key` to `client.users.create` along with `name`, `email`, and `metadata`. Do not call `client.create_user`.

The SDK keeps the first `User` stored for a key. A later create with that same key returns the original user and does not insert another record, even when `name`, `email`, or `metadata` differ. A different key creates a different user.

Return the same dictionary shape as `create_user`:

```python
{"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
```
