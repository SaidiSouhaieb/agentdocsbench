"""Verifier execution errors."""


class VerifierError(RuntimeError):
    """Raised when a verifier cannot be executed."""


class VerifierTimeoutError(VerifierError):
    """Raised when a verifier process exceeds its time limit."""


class VerifierCommandError(VerifierError):
    """Raised when a verifier command cannot be parsed into arguments."""


class VerifierExecutableNotFoundError(VerifierError):
    """Raised when the verifier executable is not available."""
