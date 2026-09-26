"""Infrastructure errors for an execution runtime.

These are not provider failure classifications. A missing Docker daemon, a
missing image, or a container that never started is an infrastructure failure.
A provider CLI that actually ran and returned a normal exit code is not.
"""


class ExecutionRuntimeError(Exception):
    """The execution runtime could not run a process.

    Callers must not pass this through provider failure classification.
    """


class RuntimeConfigError(ExecutionRuntimeError):
    """Raised when a runtime configuration file is invalid or incomplete."""


class DockerExecutableNotFoundError(ExecutionRuntimeError):
    """Raised when the Docker CLI is not on PATH."""


class DockerDaemonUnavailableError(ExecutionRuntimeError):
    """Raised when the Docker CLI cannot reach a daemon."""


class DockerImageNotFoundError(ExecutionRuntimeError):
    """Raised when a configured image is not present locally."""


class DockerContainerError(ExecutionRuntimeError):
    """Raised when a container cannot be created, started, inspected, or removed."""
