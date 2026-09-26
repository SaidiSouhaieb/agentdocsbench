"""Check retry counts without sleeping."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import NimbusAuthError, NimbusRateLimitError, NimbusServerError, NimbusValidationError  # noqa: E402
from app.retries import call_with_retry  # noqa: E402

def run(failures):
    calls = {"n": 0}
    delays = []

    def sleeper(attempt, delay):
        delays.append((attempt, delay))

    def operation():
        calls["n"] += 1
        if calls["n"] <= len(failures):
            raise failures[calls["n"] - 1]
        return "ok"

    from nimbus_sdk import RetryPolicy

    policy = RetryPolicy(sleeper=sleeper)
    try:
        result = call_with_retry(operation, policy=policy)
    except Exception as exc:
        return calls["n"], delays, exc
    return calls["n"], delays, result

count, delays, result = run([NimbusServerError(503, "unavailable"), NimbusRateLimitError("slow")])
if (count, delays, result) != (3, [(1, 0.2), (2, 0.4)], "ok"):
    print(f"Retry path failed: count={count} delays={delays} result={result}", file=sys.stderr)
    raise SystemExit(1)

count, delays, result = run([NimbusAuthError("no")])
if count != 1 or delays or not isinstance(result, NimbusAuthError):
    print(f"Auth path was retried: count={count} delays={delays} result={result}", file=sys.stderr)
    raise SystemExit(1)

count, delays, result = run([NimbusValidationError("bad")])
if count != 1 or delays or not isinstance(result, NimbusValidationError):
    print(f"Validation path was retried: count={count} delays={delays}", file=sys.stderr)
    raise SystemExit(1)

count, delays, result = run([NimbusServerError(503, "a"), NimbusServerError(502, "b"), NimbusServerError(503, "c")])
if count != 3 or not isinstance(result, NimbusServerError) or delays != [(1, 0.2), (2, 0.4)]:
    print(f"Attempt cap failed: count={count} delays={delays} result={result}", file=sys.stderr)
    raise SystemExit(1)
