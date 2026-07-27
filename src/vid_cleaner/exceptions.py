"""Exceptions raised by vid-cleaner."""

from pathlib import Path

__all__ = ["VideoCleanError", "VideoProbeError"]


class VideoCleanError(Exception):
    """Raised when one file cannot be cleaned, so a batch can skip it and continue.

    Distinct from `cappa.Exit`, which means the whole run should stop.
    """

    def __init__(self, path: Path, reason: str) -> None:
        """Initialize VideoCleanError.

        Args:
            path (Path): The file that could not be cleaned.
            reason (str): Short explanation of why the file could not be cleaned.
        """
        self.path = path
        self.reason = reason
        super().__init__(f"Could not clean '{path}': {reason}")


class VideoProbeError(Exception):
    """Raised when a file cannot be read as a video.

    Carries the offending path so callers can decide whether one bad file aborts the run or is skipped and reported at the end.
    """

    def __init__(self, path: Path, reason: str) -> None:
        """Initialize VideoProbeError.

        Args:
            path (Path): The file that could not be read.
            reason (str): Short explanation of why the file could not be read.
        """
        self.path = path
        self.reason = reason
        super().__init__(f"Could not read '{path}' as a video file: {reason}")

    @classmethod
    def from_ffprobe_stderr(cls, path: Path, stderr: bytes | str | None) -> "VideoProbeError":
        """Build an error from raw ffprobe stderr.

        ffprobe prints its banner and library versions before the actual diagnostic, so only the final line is worth surfacing to the user.

        Args:
            path (Path): The path that failed to probe.
            stderr (bytes | str | None): Everything ffprobe wrote to stderr.

        Returns:
            VideoProbeError: An error carrying ffprobe's own explanation.
        """
        text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr

        for line in reversed((text or "").splitlines()):
            if stripped := line.strip():
                return cls(path=path, reason=stripped.removeprefix(f"{path}: "))

        return cls(path=path, reason="unknown ffprobe error")
