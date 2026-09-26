"""Turn a Pydantic validation error into a short message."""

from pydantic import ValidationError


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"])
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        if location:
            lines.append(f"{location}: {message}")
        else:
            lines.append(message)
    return "\n".join(lines)
