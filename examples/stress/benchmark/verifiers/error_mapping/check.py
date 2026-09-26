"""Check SDK exception mapping."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import (  # noqa: E402
    NimbusAuthError,
    NimbusError,
    NimbusNotFoundError,
    NimbusRateLimitError,
    NimbusServerError,
)
from app.errors import AppError, handle_sdk_error  # noqa: E402

cases = [
    (NimbusAuthError("nope"), "auth_failed", 401),
    (NimbusNotFoundError("missing"), "not_found", 404),
    (NimbusRateLimitError("slow down"), "rate_limited", 429),
    (NimbusServerError(503, "down"), "nimbus_error", 500),
    (NimbusError("other"), "nimbus_error", 500),
]
for exc, code, status in cases:
    result = handle_sdk_error(exc)
    if not isinstance(result, AppError) or result.code != code or result.status != status or result.message != str(exc):
        print(f"Expected {code}/{status} for {exc!r}, found {result!r}", file=sys.stderr)
        raise SystemExit(1)
try:
    handle_sdk_error(RuntimeError("no"))
except TypeError:
    pass
else:
    print("Expected TypeError for a non-SDK exception", file=sys.stderr)
    raise SystemExit(1)
