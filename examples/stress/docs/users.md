# Users

Create users with the current service method:

```python
user = client.users.create(
    name=name,
    email=email,
    metadata=metadata or {},
    idempotency_key=None,
)
```

`name` must be non-empty. `email` must contain `@`. `metadata` is a dictionary of strings. The method returns a `User` with `id`, `name`, `email`, and `metadata`.

## Application result

`app.users.create_user(client, *, name, email, metadata=None)` returns:

```python
{"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
```

Use `client.users.create`. Do not call `client.create_user`.

## Idempotency

Idempotent creates are specified in `idempotency.md`. `create_user` itself does not send an idempotency key.

## Lookup

`client.users.get(user_id)` returns the `User` or raises `NimbusNotFoundError`.

`client.users.seed(users)` replaces the local directory with an existing list of `User` objects. The CLI uses it. See `cli.md` and `pagination.md`.
