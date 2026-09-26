# Retries

`nimbus_sdk.RetryPolicy` does not call the operation. `app.retries.call_with_retry(operation, *, policy=None)` owns the loop.

When `policy` is omitted, construct `RetryPolicy()`. Number attempts from 1. Call `operation`. If it raises and `policy.should_retry(exc, attempt)` is false, propagate that exception. Otherwise call `policy.sleeper(attempt, policy.delay_seconds(attempt))`, then try again with the next attempt number. Do not call `time.sleep`.

Rules implemented by `RetryPolicy`:

- `max_attempts` defaults to 3 total calls.
- Retry only status codes 429, 502, and 503.
- Do not retry 400, 401, 403, or 404.
- Do not retry exceptions that are not `NimbusError`.
- `should_retry(exc, attempt)` is false when `attempt` is already `max_attempts`.
- After failed attempt 1 the delay is 0.2 seconds. After attempt 2 it is 0.4. The formula is `0.2 * 2 ** (attempt - 1)`.
- Call `policy.sleeper(attempt, delay)` with those values. Do not call `time.sleep`.

`NimbusRateLimitError` is status 429. Construct `NimbusServerError(502, "bad gateway")` or `NimbusServerError(503, "unavailable")` for server failures. See `errors.md`.
