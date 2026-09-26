"""Application retry loop. See docs/retries.md."""


def call_with_retry(operation, *, policy=None):
    raise NotImplementedError("Retry only the documented status codes.")
