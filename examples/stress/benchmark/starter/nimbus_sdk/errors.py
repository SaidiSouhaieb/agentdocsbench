"""SDK exceptions. Status codes are part of the public error contract."""


class NimbusError(Exception):
    """Base class for every Nimbus SDK failure."""

    status_code = 500


class NimbusConfigError(NimbusError):
    """Raised when configuration cannot be resolved."""

    status_code = 500


class NimbusAuthError(NimbusError):
    """Raised when credentials are rejected."""

    status_code = 401


class NimbusPermissionError(NimbusError):
    """Raised when the caller is authenticated but not allowed."""

    status_code = 403


class NimbusNotFoundError(NimbusError):
    """Raised when a record does not exist."""

    status_code = 404


class NimbusValidationError(NimbusError):
    """Raised when a request is rejected as invalid."""

    status_code = 400


class NimbusRateLimitError(NimbusError):
    """Raised when the caller should back off."""

    status_code = 429


class NimbusServerError(NimbusError):
    """Raised for a retryable or unexpected server status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
