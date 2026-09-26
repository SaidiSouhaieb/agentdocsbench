"""Check new_dashboard enabled, disabled, and unavailable."""

import os
import sys
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
sys.path.insert(0, str(project))

from nimbus_sdk import FeatureFlags  # noqa: E402
from app.flags import dashboard_variant  # noqa: E402

cases = [
    (FeatureFlags({"new_dashboard": True}), "new"),
    (FeatureFlags({"new_dashboard": False}), "classic"),
    (FeatureFlags({}), "classic"),
    (FeatureFlags({"new_dashboard": True}, unavailable=True), "classic"),
]
for flags, expected in cases:
    found = dashboard_variant(flags)
    if found != expected:
        print(f"Expected {expected}, found {found}", file=sys.stderr)
        raise SystemExit(1)
