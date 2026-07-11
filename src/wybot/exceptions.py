"""Exceptions for the WyBot API client."""


class WybotError(Exception):
    """Base error for the WyBot client."""


class WybotConnectionError(WybotError):
    """Raised when the WyBot API cannot be reached (network/server error)."""


class WybotAuthError(WybotError):
    """Raised when authentication with the WyBot API fails (invalid credentials)."""
