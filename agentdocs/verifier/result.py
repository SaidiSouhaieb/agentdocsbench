"""Result of one verifier process."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifierResult:
    """Outcome of one verifier process. Exit code 0 is the only pass."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.exit_code == 0
