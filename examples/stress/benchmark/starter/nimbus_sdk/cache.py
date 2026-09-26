"""Process-local cache with an injected clock."""


class MemoryCache:
    """Store values until ``clock()`` reaches ``stored_at + ttl_seconds``.

    ``clock`` returns a number. Tests pass a lambda so nothing sleeps.
    A missing clock always returns 0, so entries do not expire unless
    invalidated.
    """

    def __init__(self, clock=None) -> None:
        self._clock = clock if clock is not None else (lambda: 0)
        self._items: dict[str, tuple[float, float, object]] = {}

    def get(self, key: str):
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, ttl_seconds, value = item
        if self._clock() >= stored_at + ttl_seconds:
            del self._items[key]
            return None
        return value

    def set(self, key: str, value, ttl_seconds: float) -> None:
        self._items[key] = (self._clock(), ttl_seconds, value)

    def invalidate(self, key: str) -> None:
        self._items.pop(key, None)
