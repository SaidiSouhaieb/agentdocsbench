"""Check configuration precedence without reading the process environment."""

import json
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from app.config import load_app_config  # noqa: E402

path = project / "config" / "layered.json"
path.write_text(
    json.dumps(
        {
            "api_key": "from-file",
            "base_url": "https://file.nimbus.local",
            "timeout_seconds": 15,
            "api_version": "2024-01-01",
        }
    ),
    encoding="utf-8",
)
env = {
    "NIMBUS_API_KEY": "from-env",
    "NIMBUS_BASE_URL": "https://env.nimbus.local",
    "NIMBUS_TIMEOUT_SECONDS": "20",
    "NIMBUS_API_VERSION": "2024-03-01",
}
explicit = {"api_key": "from-explicit", "timeout_seconds": 30.0}
found = load_app_config(path, explicit=explicit, env=env)
expected = {
    "api_key": "from-explicit",
    "base_url": "https://env.nimbus.local",
    "timeout_seconds": 30.0,
    "api_version": "2024-03-01",
}
if found != expected:
    print(f"Mixed precedence expected {expected}, found {found}", file=sys.stderr)
    raise SystemExit(1)

defaults = load_app_config(path, explicit={"api_key": "only-key"}, env={})
if defaults["base_url"] != "https://file.nimbus.local" or defaults["timeout_seconds"] != 15.0:
    print(f"File fallback failed: {defaults}", file=sys.stderr)
    raise SystemExit(1)

empty = project / "config" / "empty.json"
empty.write_text("{}", encoding="utf-8")
bare = load_app_config(empty, explicit={"api_key": "only-key"}, env={})
if bare != {
    "api_key": "only-key",
    "base_url": "https://api.nimbus.local",
    "timeout_seconds": 10.0,
    "api_version": "2024-06-01",
}:
    print(f"Defaults failed: {bare}", file=sys.stderr)
    raise SystemExit(1)
try:
    load_app_config(empty, explicit={}, env={})
except Exception as exc:
    if exc.__class__.__name__ != "NimbusConfigError":
        print(f"Expected NimbusConfigError, found {exc!r}", file=sys.stderr)
        raise SystemExit(1)
else:
    print("Missing api_key was accepted", file=sys.stderr)
    raise SystemExit(1)
