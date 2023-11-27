"""Custom errors for the vid-cleaner package."""


class SameFileError(OSError):
    """Raised when source and destination are the same file."""
