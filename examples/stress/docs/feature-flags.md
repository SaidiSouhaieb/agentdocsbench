# Feature flags

`nimbus_sdk.FeatureFlags(values=None, *, unavailable=False)`.

`flags.is_enabled(name, *, default)` returns:

- `default` when the service is unavailable
- `default` when `name` is not present
- the stored boolean otherwise

`app.flags.dashboard_variant(flags)` reads the flag `new_dashboard` with `default=False`.

- Enabled: return `"new"`.
- Disabled, missing, or unavailable: return `"classic"`.

There is no other flag name for this helper.
