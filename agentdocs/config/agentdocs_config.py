"""Top-level AgentDocsBench project configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from agentdocs.config.agent_config import AgentConfig
from agentdocs.config.constants import SUPPORTED_VERSIONS
from agentdocs.config.task_config import TaskConfig


class AgentDocsConfig(BaseModel):
    """Top-level AgentDocsBench project configuration."""

    model_config = ConfigDict(extra="forbid")

    version: int
    docs: Path
    starter: Path
    agent: AgentConfig
    tasks: list[TaskConfig]

    @field_validator("version")
    @classmethod
    def version_must_be_supported(cls, version: int) -> int:
        if version not in SUPPORTED_VERSIONS:
            supported = ", ".join(str(item) for item in sorted(SUPPORTED_VERSIONS))
            raise ValueError(
                f"Unsupported version {version}. Supported versions: {supported}."
            )
        return version

    @field_validator("tasks")
    @classmethod
    def tasks_must_not_be_empty(cls, tasks: list[TaskConfig]) -> list[TaskConfig]:
        if not tasks:
            raise ValueError("At least one task is required.")
        return tasks

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> AgentDocsConfig:
        seen: set[str] = set()
        duplicates: list[str] = []
        for task in self.tasks:
            if task.id in seen and task.id not in duplicates:
                duplicates.append(task.id)
            seen.add(task.id)
        if duplicates:
            joined = ", ".join(repr(task_id) for task_id in duplicates)
            raise ValueError(
                f"Duplicate task id(s): {joined}. Task ids must be unique."
            )
        return self
