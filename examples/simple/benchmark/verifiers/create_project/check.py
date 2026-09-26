import os
from pathlib import Path

project = Path(os.environ["AGENTDOCS_PROJECT_DIR"])
path = project / "project.txt"

if not path.exists():
    raise SystemExit("project.txt does not exist")

value = path.read_text().strip()

if value != "AgentDocsBench":
    raise SystemExit(f"Expected AgentDocsBench, found {value!r}")
