"""Check valid, invalid, and unsupported webhooks."""

import json
import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import WebhookVerifier  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.webhooks import accept_webhook  # noqa: E402

secret = "whsec_test"
body = json.dumps({"type": "user.created", "data": {"id": "user-9"}}).encode("utf-8")
signature = WebhookVerifier(secret).sign(body)
accepted = accept_webhook(body, signature, secret=secret)
if accepted != {"type": "user.created", "data": {"id": "user-9"}}:
    print(f"Valid event mismatch {accepted}", file=sys.stderr)
    raise SystemExit(1)
try:
    accept_webhook(body, "v1=deadbeef", secret=secret)
except AppError as exc:
    if exc.code != "invalid_signature" or exc.status != 401:
        print(f"Invalid signature mapped wrong: {exc.code} {exc.status}", file=sys.stderr)
        raise SystemExit(1)
else:
    print("Invalid signature was accepted", file=sys.stderr)
    raise SystemExit(1)

other = json.dumps({"type": "invoice.paid", "data": {}}).encode("utf-8")
try:
    accept_webhook(other, WebhookVerifier(secret).sign(other), secret=secret)
except AppError as exc:
    if exc.code != "unsupported_event" or exc.status != 400:
        print(f"Unsupported event mapped wrong: {exc.code} {exc.status}", file=sys.stderr)
        raise SystemExit(1)
else:
    print("Unsupported event was accepted", file=sys.stderr)
    raise SystemExit(1)
