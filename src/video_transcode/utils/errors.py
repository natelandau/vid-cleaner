"""Custom errors for the video_transcode package."""


class SameFileError(OSError):
    """Raised when source and destination are the same file."""
