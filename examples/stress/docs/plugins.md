# Plugins

A plugin has a non-empty string `name` and `on_user_created(user)`.

`nimbus_sdk.PluginRegistry`:

- `register(plugin)` stores it under `plugin.name`. Registering the same name twice raises `ValueError`.
- `get(name)` returns that plugin or raises `KeyError`.
- `notify_user_created(user)` calls `on_user_created` on every registered plugin. Application code must not call `on_user_created` itself when a user is created; it calls `notify_user_created`.

`app.plugins.AuditPlugin` has `name = "audit"`. `on_user_created` appends `user.id` to `self.seen`.

`app.plugins.build_registry()` returns a `PluginRegistry` with one `AuditPlugin` registered.
