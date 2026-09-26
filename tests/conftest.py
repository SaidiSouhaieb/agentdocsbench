"""Keep CLI snapshots plain so GitHub Actions color does not change them."""

from __future__ import annotations

import os

# Typer enables a color terminal when GITHUB_ACTIONS is set. Rich then inserts
# escape codes inside option names, so "--config" is no longer one substring.
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
