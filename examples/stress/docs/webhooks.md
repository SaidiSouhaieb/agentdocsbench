# Webhooks

`nimbus_sdk.WebhookVerifier(secret)` checks signatures and parses JSON bodies.

The signature is `v1=` plus the hex SHA-256 of `secret.encode("utf-8") + b"." + body`. `body` is raw bytes. Use `verifier.verify(body, signature)` rather than reimplementing the digest in application code. `verifier.sign(body)` produces the expected header for a secret.

Supported event types are `user.created` and `organization.member_added`. `verifier.parse_event(body)` returns a `WebhookEvent` with `type` and `data`, or raises `ValueError` for any other type. The body is JSON: `{"type": "...", "data": {...}}`.

`app.webhooks.accept_webhook(body, signature, *, secret)` must:

1. Build a `WebhookVerifier` with `secret`.
2. If `verify` is false, raise `AppError("invalid_signature", "Invalid webhook signature.", 401)`.
3. Parse the event.
4. If parsing raises `ValueError`, raise `AppError("unsupported_event", "Unsupported webhook event.", 400)`.
5. Otherwise return `{"type": event.type, "data": dict(event.data)}`.

See `authentication.md` for why the secret is an argument, and `errors.md` for `AppError`.
