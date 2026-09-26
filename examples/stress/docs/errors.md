# Errors

SDK exceptions and their `status_code` values:

| Exception | Status |
| --- | --- |
| `NimbusValidationError` | 400 |
| `NimbusAuthError` | 401 |
| `NimbusPermissionError` | 403 |
| `NimbusNotFoundError` | 404 |
| `NimbusRateLimitError` | 429 |
| `NimbusServerError` | the status passed to its constructor, usually 502 or 503 |
| other `NimbusError` | 500 |

`app.errors.handle_sdk_error(exc)` returns an `AppError`. It does not raise.

| SDK exception | `code` | `status` |
| --- | --- | --- |
| `NimbusAuthError` | `auth_failed` | 401 |
| `NimbusNotFoundError` | `not_found` | 404 |
| `NimbusRateLimitError` | `rate_limited` | 429 |
| any other `NimbusError` | `nimbus_error` | 500 |

`AppError.message` is `str(exc)`. If `exc` is not a `NimbusError`, raise `TypeError`.

Import the exception classes from `nimbus_sdk`.
