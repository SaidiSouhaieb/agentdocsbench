# Configuration

`app.config.load_app_config(path, *, explicit=None, env=None)` returns a dictionary:

```python
{
    "api_key": str,
    "base_url": str,
    "timeout_seconds": float,
    "api_version": str,
}
```

## Precedence

For each field, use the first source that provides it:

1. `explicit` argument dictionary
2. `env` argument dictionary
3. the JSON object in `path`
4. the documented default

`explicit` and the JSON file use the field names `api_key`, `base_url`, `timeout_seconds`, and `api_version`.

`env` uses different names:

| Field | Env key |
| --- | --- |
| `api_key` | `NIMBUS_API_KEY` |
| `base_url` | `NIMBUS_BASE_URL` |
| `timeout_seconds` | `NIMBUS_TIMEOUT_SECONDS` |
| `api_version` | `NIMBUS_API_VERSION` |

Do not read `os.environ`. Only the `env` argument counts. Missing `explicit` or `env` means that layer is empty. A JSON field that is absent does not count as provided.

## Defaults

| Field | Default |
| --- | --- |
| `base_url` | `https://api.nimbus.local` |
| `timeout_seconds` | `10` as a float |
| `api_version` | `2024-06-01` |

`api_key` has no default. If no layer provides it, raise `nimbus_sdk.NimbusConfigError`.

Coerce `timeout_seconds` with `float(...)` when it comes from JSON or `env`. Keep `explicit["timeout_seconds"]` as a float as given.

This loader is separate from `app.client.build_client`, which reads one complete JSON file. See `getting-started.md`.
