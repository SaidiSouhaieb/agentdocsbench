"""Feature-flagged dashboard selection. See docs/feature-flags.md."""


def dashboard_variant(flags) -> str:
    raise NotImplementedError("Return new or classic from the new_dashboard flag.")
