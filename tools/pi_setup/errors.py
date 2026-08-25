"""Exception types shared across pi-setup."""
from __future__ import annotations


class ToolError(Exception):
    """User-facing failure; the message is printed to stderr by main()."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class UsageError(ToolError):
    pass


class AuthParseError(ToolError):
    pass


class CatalogError(ToolError):
    pass


class AbortError(ToolError):
    pass


class HelpRequest(Exception):
    """Internal: -h/--help was seen and usage() already printed."""
