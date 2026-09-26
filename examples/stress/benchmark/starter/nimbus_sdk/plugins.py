"""Plugin registry. Application code registers plugins; it does not call them directly."""


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, object] = {}

    def register(self, plugin: object) -> None:
        name = getattr(plugin, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("Plugin.name must be a non-empty string.")
        if name in self._plugins:
            raise ValueError(f"Plugin {name!r} is already registered.")
        self._plugins[name] = plugin

    def get(self, name: str):
        if name not in self._plugins:
            raise KeyError(name)
        return self._plugins[name]

    def notify_user_created(self, user) -> None:
        for plugin in self._plugins.values():
            plugin.on_user_created(user)
