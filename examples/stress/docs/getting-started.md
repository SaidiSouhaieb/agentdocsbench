# Getting started

Nimbus is a local developer SDK. Nothing in this SDK contacts the network. Construct a client from `NimbusConfig` and call the service objects on it.

## Client

```python
from pathlib import Path
import json
from nimbus_sdk import NimbusClient, NimbusConfig

def build_client(config_path: Path) -> NimbusClient:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    config = NimbusConfig(
        api_key=payload["api_key"],
        base_url=payload["base_url"],
        timeout_seconds=payload["timeout_seconds"],
        api_version=payload["api_version"],
    )
    return NimbusClient(config)
```

Implement that function in `app.client.build_client`. The sample file `config/app.json` contains all four fields. Pass them through unchanged. `timeout_seconds` is a number.

`NimbusClient` rejects a missing `api_key` and rejects anything that is not a `NimbusConfig`.

## Application modules

| Behavior | Function |
| --- | --- |
| Build a client from one JSON file | `app.client.build_client` |
| Resolve layered settings | `app.config.load_app_config` |
| Create a user | `app.users.create_user` |
| Create a user with an idempotency key | `app.users.create_user_idempotent` |
| List every user | `app.users.list_all_users` |
| Add an organization member | `app.organizations.add_user_to_organization` |
| Map SDK exceptions | `app.errors.handle_sdk_error` |
| Retry an operation | `app.retries.call_with_retry` |
| Accept a webhook | `app.webhooks.accept_webhook` |
| Read a user through the cache | `app.cache.get_user` |
| Choose a dashboard variant | `app.flags.dashboard_variant` |
| Register the audit plugin | `app.plugins.build_registry` |
| List users from the CLI | `python -m app.cli users list` |
| Provision a user without deprecated calls | `app.legacy.provision_user` |
| Import a paged directory into an organization | `app.sync.import_organization_users` |

`app.errors.AppError` already exists. Its constructor is `AppError(code, message, status)`.

## Current API

Current user creation is `client.users.create(...)`. `client.create_user(...)` is deprecated. See `migrations.md`.
