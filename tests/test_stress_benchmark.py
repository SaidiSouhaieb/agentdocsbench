"""Local checks for the Nimbus stress fixture. These tests never call Codex."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentdocs import load_config, validate_config_paths

ROOT = Path(__file__).resolve().parents[1] / "examples" / "stress"
STARTER = ROOT / "benchmark" / "starter"
VERIFIERS = ROOT / "benchmark" / "verifiers"

REFERENCE = {
    "app/client.py": """
import json
from pathlib import Path
from nimbus_sdk import NimbusClient, NimbusConfig

def build_client(config_path: Path) -> NimbusClient:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    return NimbusClient(NimbusConfig(
        api_key=payload["api_key"],
        base_url=payload["base_url"],
        timeout_seconds=payload["timeout_seconds"],
        api_version=payload["api_version"],
    ))
""",
    "app/users.py": """
def _dump(user):
    return {"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}

def create_user(client, *, name, email, metadata=None):
    user = client.users.create(name=name, email=email, metadata=metadata or {})
    return _dump(user)

def create_user_idempotent(client, *, name, email, metadata, idempotency_key):
    user = client.users.create(
        name=name, email=email, metadata=metadata or {}, idempotency_key=idempotency_key
    )
    return _dump(user)

def list_all_users(client):
    cursor = None
    found = []
    while True:
        page = client.users.list(cursor=cursor, page_size=2)
        found.extend(_dump(user) for user in page.items)
        if page.next_cursor is None:
            return found
        cursor = page.next_cursor
""",
    "app/organizations.py": """
def add_user_to_organization(client, *, org_name, user_name, user_email, role):
    org = client.organizations.create(name=org_name)
    user = client.users.create(name=user_name, email=user_email, metadata={})
    membership = client.organizations.add_member(org.id, user_id=user.id, role=role)
    return {"organization_id": org.id, "user_id": user.id, "role": membership.role}
""",
    "app/errors.py": """
from nimbus_sdk import NimbusAuthError, NimbusError, NimbusNotFoundError, NimbusRateLimitError

class AppError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

def handle_sdk_error(exc):
    if not isinstance(exc, NimbusError):
        raise TypeError("Expected a NimbusError.")
    if isinstance(exc, NimbusAuthError):
        return AppError("auth_failed", str(exc), 401)
    if isinstance(exc, NimbusNotFoundError):
        return AppError("not_found", str(exc), 404)
    if isinstance(exc, NimbusRateLimitError):
        return AppError("rate_limited", str(exc), 429)
    return AppError("nimbus_error", str(exc), 500)
""",
    "app/retries.py": """
from nimbus_sdk import RetryPolicy

def call_with_retry(operation, *, policy=None):
    policy = policy or RetryPolicy()
    attempt = 1
    while True:
        try:
            return operation()
        except Exception as exc:
            if not policy.should_retry(exc, attempt):
                raise
            policy.sleeper(attempt, policy.delay_seconds(attempt))
            attempt += 1
""",
    "app/webhooks.py": """
from nimbus_sdk import WebhookVerifier
from app.errors import AppError

def accept_webhook(body, signature, *, secret):
    verifier = WebhookVerifier(secret)
    if not verifier.verify(body, signature):
        raise AppError("invalid_signature", "Invalid webhook signature.", 401)
    try:
        event = verifier.parse_event(body)
    except ValueError:
        raise AppError("unsupported_event", "Unsupported webhook event.", 400) from None
    return {"type": event.type, "data": dict(event.data)}
""",
    "app/cache.py": """
def get_user(client, cache, user_id):
    key = f"user:{user_id}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached
    user = client.users.get(user_id)
    payload = {"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
    cache.set(key, payload, ttl_seconds=60)
    return payload
""",
    "app/flags.py": """
def dashboard_variant(flags):
    return "new" if flags.is_enabled("new_dashboard", default=False) else "classic"
""",
    "app/plugins.py": """
from nimbus_sdk import PluginRegistry

class AuditPlugin:
    name = "audit"
    def __init__(self):
        self.seen = []
    def on_user_created(self, user):
        self.seen.append(user.id)

def build_registry():
    registry = PluginRegistry()
    registry.register(AuditPlugin())
    return registry
""",
    "app/config.py": """
import json
from pathlib import Path
from nimbus_sdk import NimbusConfigError

def load_app_config(path, *, explicit=None, env=None):
    explicit = dict(explicit or {})
    env = dict(env or {})
    file_data = json.loads(Path(path).read_text(encoding="utf-8"))
    env_keys = {
        "api_key": "NIMBUS_API_KEY",
        "base_url": "NIMBUS_BASE_URL",
        "timeout_seconds": "NIMBUS_TIMEOUT_SECONDS",
        "api_version": "NIMBUS_API_VERSION",
    }
    defaults = {
        "base_url": "https://api.nimbus.local",
        "timeout_seconds": 10.0,
        "api_version": "2024-06-01",
    }
    resolved = {}
    for field in ("api_key", "base_url", "timeout_seconds", "api_version"):
        if field in explicit:
            value = explicit[field]
        elif env_keys[field] in env:
            value = env[env_keys[field]]
        elif field in file_data:
            value = file_data[field]
        elif field in defaults:
            value = defaults[field]
        else:
            raise NimbusConfigError("api_key is required.")
        if field == "timeout_seconds":
            value = float(value)
        resolved[field] = value
    return resolved
""",
    "app/cli.py": """
import json
import sys
from pathlib import Path
from nimbus_sdk import NimbusClient, NimbusConfig, User

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 4 and args[:3] == ["users", "list", "--directory"]:
        payload = json.loads(Path(args[3]).read_text(encoding="utf-8"))
        users = [
            User(
                id=item["id"],
                name=item["name"],
                email=item["email"],
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload
        ]
        client = NimbusClient(NimbusConfig(api_key="nim_local"))
        client.users.seed(users)
        cursor = None
        while True:
            page = client.users.list(cursor=cursor, page_size=2)
            for user in page.items:
                print(f"{user.id} {user.email}")
            if page.next_cursor is None:
                return 0
            cursor = page.next_cursor
    print("Unknown command", file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "app/legacy.py": """
def provision_user(client, *, name, email, metadata=None):
    user = client.users.create(name=name, email=email, metadata=metadata or {})
    return {"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
""",
    "app/sync.py": """
from nimbus_sdk import NimbusAuthError
from app.errors import AppError
from app.retries import call_with_retry

def import_organization_users(client, cache, *, org_name, directory):
    cursor = None
    users = []
    while True:
        def fetch(current=cursor):
            try:
                return directory.list_page(cursor=current, page_size=2)
            except NimbusAuthError as exc:
                raise AppError("auth_failed", str(exc), 401) from exc
        page = call_with_retry(fetch)
        users.extend(page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    org = client.organizations.create(name=org_name)
    ids = []
    for user in users:
        client.organizations.add_member(org.id, user_id=user.id, role="member")
        cache.invalidate(f"user:{user.id}")
        ids.append(user.id)
    return {"organization_id": org.id, "imported_user_ids": ids, "member_count": len(ids)}
""",
}


def _task_ids() -> list[str]:
    config = load_config(ROOT / "agentdocs.yaml")
    return [task.id for task in config.tasks]


def _run(project: Path, task_id: str) -> int:
    completed = subprocess.run(
        [sys.executable, "check.py"],
        cwd=VERIFIERS / task_id,
        capture_output=True,
        text=True,
        env={**os.environ, "AGENTDOCS_PROJECT_DIR": str(project)},
        check=False,
    )
    return completed.returncode


def test_stress_config_loads() -> None:
    config = load_config(ROOT / "agentdocs.yaml")
    validate_config_paths(config)
    ids = [task.id for task in config.tasks]
    assert len(ids) == 15
    assert len(set(ids)) == 15
    assert config.agent.type == "codex"
    for task in config.tasks:
        assert task.verify.path.is_dir()
        assert task.verify.command == "python3 check.py"


def test_starter_imports() -> None:
    starter = str(STARTER)
    sys.path.insert(0, starter)
    try:
        import app.client  # noqa: F401
        from nimbus_sdk import NimbusClient, NimbusConfig

        client = NimbusClient(NimbusConfig(api_key="nim_test"))
        user = client.users.create(name="Ada", email="ada@nimbus.local", metadata={})
        assert user.id == "user-1"
    finally:
        if starter in sys.path:
            sys.path.remove(starter)


@pytest.mark.parametrize("task_id", _task_ids())
def test_untouched_starter_fails(task_id: str, tmp_path: Path) -> None:
    project = tmp_path / "starter"
    shutil.copytree(STARTER, project)
    assert _run(project, task_id) != 0


@pytest.mark.parametrize("task_id", _task_ids())
def test_reference_solution_passes(task_id: str, tmp_path: Path) -> None:
    project = tmp_path / "solved"
    shutil.copytree(STARTER, project)
    for relative, source in REFERENCE.items():
        (project / relative).write_text(source.lstrip("\n"), encoding="utf-8")
    assert _run(project, task_id) == 0
