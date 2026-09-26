"""Audit plugin. See docs/plugins.md."""


class AuditPlugin:
    name = "audit"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def on_user_created(self, user) -> None:
        raise NotImplementedError("Record the created user id.")


def build_registry():
    raise NotImplementedError("Register AuditPlugin through PluginRegistry.")
