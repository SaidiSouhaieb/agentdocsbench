"""Local webhook signatures. No network and no third-party crypto library."""

import hashlib
import json
from dataclasses import dataclass

_SUPPORTED_EVENTS = frozenset({"user.created", "organization.member_added"})


@dataclass(frozen=True)
class WebhookEvent:
    type: str
    data: dict


class WebhookVerifier:
    """Verify ``v1=`` signatures and parse a JSON event body."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("Webhook secret must not be empty.")
        self._secret = secret

    def sign(self, body: bytes) -> str:
        digest = hashlib.sha256(self._secret.encode("utf-8") + b"." + body).hexdigest()
        return f"v1={digest}"

    def verify(self, body: bytes, signature: str) -> bool:
        if not isinstance(body, bytes) or not isinstance(signature, str):
            return False
        return signature == self.sign(body)

    def parse_event(self, body: bytes) -> WebhookEvent:
        payload = json.loads(body.decode("utf-8"))
        event_type = payload["type"]
        if event_type not in _SUPPORTED_EVENTS:
            raise ValueError(f"Unsupported webhook event {event_type!r}.")
        return WebhookEvent(type=event_type, data=dict(payload["data"]))
