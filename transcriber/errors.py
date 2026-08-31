"""Domain errors surfaced to the browser as ``{"error": ...}``."""


class AppError(Exception):
    """Recoverable, user-facing failure."""
