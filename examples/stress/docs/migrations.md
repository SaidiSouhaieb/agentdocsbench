# Migrations

API version `2024-06-01` removed positional user creation.

Deprecated:

```python
client.create_user(name, email, metadata)
```

Current:

```python
client.users.create(name=name, email=email, metadata=metadata or {})
```

`client.create_user` still runs, but application code must not call it. `app.legacy.provision_user(client, *, name, email, metadata=None)` still uses the deprecated method. Change that function so it calls `client.users.create` and returns:

```python
{"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
```

Leave the function name `provision_user` in place. Do not add a new call to `create_user` anywhere in that function. See `users.md` for the return shape and `getting-started.md` for the current client.
