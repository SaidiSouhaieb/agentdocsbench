"""Load an AgentDocsBench config file."""

from pathlib import Path

from agentdocs.config.agentdocs_config import AgentDocsConfig
from agentdocs.config.errors import ConfigError
from agentdocs.config.read_yaml_mapping import _read_yaml_mapping
from agentdocs.config.resolve_against_config_dir import _resolve_against_config_dir
from agentdocs.config.resolve_task_paths import _resolve_task_paths
from agentdocs.config.validate_data import _validate_data


def load_config(path: str | Path = "agentdocs.yaml") -> AgentDocsConfig:
    """Load an AgentDocsBench config file.

    Relative ``docs``, ``starter``, and ``verify.path`` values are resolved
    against the directory that contains the config file, not the process
    working directory. Absolute paths are kept as absolute paths. This
    function validates the schema only. Call :func:`validate_config_paths`
    to check that those directories exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    if not config_path.is_file():
        raise ConfigError(f"Config path is not a file: {config_path}")

    data = _read_yaml_mapping(config_path)
    config = _validate_data(config_path, data)
    base_dir = config_path.resolve().parent
    return config.model_copy(
        update={
            "docs": _resolve_against_config_dir(base_dir, config.docs),
            "starter": _resolve_against_config_dir(base_dir, config.starter),
            "tasks": _resolve_task_paths(base_dir, config.tasks),
        }
    )
