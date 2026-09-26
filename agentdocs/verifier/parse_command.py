"""Parse a verifier command into an argument list."""

import shlex

from agentdocs.verifier.errors import VerifierCommandError


def _parse_command(command: str) -> tuple[str, ...]:
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise VerifierCommandError(
            f"Verifier command could not be parsed: {exc}"
        ) from exc
    if not args or not args[0].strip():
        raise VerifierCommandError(
            "Verifier command did not contain an executable."
        )
    return tuple(args)
