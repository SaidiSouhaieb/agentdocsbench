"""Check that config directories exist on disk."""

from agentdocs.config.agentdocs_config import AgentDocsConfig
from agentdocs.config.require_directory import _require_directory
from agentdocs.config.require_verifier_directory import _require_verifier_directory


def validate_config_paths(config: AgentDocsConfig) -> None:
    """Ensure docs, starter, and each verifier directory exist.

    Schema validation does not touch the filesystem. Call this after
    :func:`load_config` when the directories must be present. Verifier
    commands are not checked for an executable on disk.
    """
    _require_directory("docs", config.docs)
    _require_directory("starter", config.starter)
    for task in config.tasks:
        _require_verifier_directory(task)
