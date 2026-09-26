"""Local feature-flag lookup."""


class FeatureFlags:
    """Resolve boolean flags.

    When ``unavailable`` is true, or the name is absent, ``is_enabled``
    returns ``default``.
    """

    def __init__(self, values: dict[str, bool] | None = None, *, unavailable: bool = False) -> None:
        self._values = dict(values or {})
        self._unavailable = unavailable

    def is_enabled(self, name: str, *, default: bool) -> bool:
        if self._unavailable or name not in self._values:
            return default
        return bool(self._values[name])
