# Authentication

The API key lives in configuration, not in source code. See `configuration.md` for precedence.

Webhook signatures use a separate shared secret. That secret is an argument to `app.webhooks.accept_webhook`; it is not read from the process environment. The signature algorithm is in `webhooks.md`.

Authentication failures raised by the SDK are `NimbusAuthError` with `status_code` 401. Application code maps that error as described in `errors.md`. Do not retry it. See `retries.md`.
