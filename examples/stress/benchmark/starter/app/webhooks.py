"""Webhook acceptance. See docs/webhooks.md."""


def accept_webhook(body: bytes, signature: str, *, secret: str) -> dict:
    raise NotImplementedError("Verify the signature and return the supported event.")
