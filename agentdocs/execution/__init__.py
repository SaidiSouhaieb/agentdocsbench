"""Choose the default local runtime. Docker is constructed by the CLI."""

from agentdocs.execution.docker import DockerExecutionRuntime
from agentdocs.execution.local import LocalExecutionRuntime

__all__ = [
    "DockerExecutionRuntime",
    "LocalExecutionRuntime",
]
