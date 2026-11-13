"""Error categories and exit codes shared by every command."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class ErrorCategory(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS_SELECTION = "ambiguous_selection"
    PERMISSION = "permission"
    UNSUPPORTED = "unsupported"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    INVALID_INPUT = 2  # matches argparse's own usage errors
    UNAVAILABLE = 3
    AMBIGUOUS_SELECTION = 4
    PERMISSION = 5
    UNSUPPORTED = 6
    TIMEOUT = 7
    PROTOCOL = 8
    WRITE_UNCERTAIN = 9
    WRITE_NOT_APPLIED = 10
    INTERRUPTED = 130


_EXIT_CODES = {
    ErrorCategory.INVALID_INPUT: ExitCode.INVALID_INPUT,
    ErrorCategory.UNAVAILABLE: ExitCode.UNAVAILABLE,
    ErrorCategory.AMBIGUOUS_SELECTION: ExitCode.AMBIGUOUS_SELECTION,
    ErrorCategory.PERMISSION: ExitCode.PERMISSION,
    ErrorCategory.UNSUPPORTED: ExitCode.UNSUPPORTED,
    ErrorCategory.TIMEOUT: ExitCode.TIMEOUT,
    ErrorCategory.PROTOCOL: ExitCode.PROTOCOL,
}


class ShokzError(Exception):
    """An expected failure with a category, a user-facing message and an optional hint."""

    def __init__(self, category: ErrorCategory, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.hint = hint

    @property
    def exit_code(self) -> ExitCode:
        return _EXIT_CODES[self.category]
