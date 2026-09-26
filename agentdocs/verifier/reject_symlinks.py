"""Reject symbolic links in a verifier source tree."""

from pathlib import Path

from agentdocs._symlinks import find_symlink
from agentdocs.verifier.errors import VerifierError


def _reject_symlinks(source: Path) -> None:
    link = find_symlink(source)
    if link is None:
        return
    raise VerifierError(
        "Verifier source contains symbolic link:\n"
        f"{link}\n\n"
        "Symbolic links are not supported in AgentDocsBench verifiers yet."
    )
