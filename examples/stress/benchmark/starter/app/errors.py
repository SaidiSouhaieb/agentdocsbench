"""Documented application error."""


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def handle_sdk_error(exc: BaseException) -> AppError:
    raise NotImplementedError("Map Nimbus SDK errors to AppError. See docs/errors.md.")
