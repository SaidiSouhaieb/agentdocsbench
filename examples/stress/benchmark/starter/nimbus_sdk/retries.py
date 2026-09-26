"""Retry decisions. The application owns the attempt loop."""

from nimbus_sdk.errors import NimbusError

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503})


class RetryPolicy:
    """Decides whether a failed attempt may be repeated.

    ``max_attempts`` is the total number of calls, not the number of retries.
    ``sleeper`` is ``sleeper(failed_attempt, delay_seconds)``. It must not be
    required to sleep; tests pass a recorder.
    """

    def __init__(self, max_attempts: int = 3, sleeper=None) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self.max_attempts = max_attempts
        self.sleeper = sleeper if sleeper is not None else (lambda _attempt, _delay: None)

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        """Return true when ``attempt`` failed and another call is allowed.

        ``attempt`` is 1-based. A third failure is never retried when
        ``max_attempts`` is 3.
        """
        if attempt >= self.max_attempts:
            return False
        if not isinstance(exc, NimbusError):
            return False
        return exc.status_code in _RETRYABLE_STATUS_CODES

    def delay_seconds(self, attempt: int) -> float:
        """Backoff after failed attempt ``attempt`` (1-based): 0.2, 0.4, 0.8, ..."""
        if attempt < 1:
            raise ValueError("attempt must be 1-based.")
        return 0.2 * (2 ** (attempt - 1))
