"""Utilities for CLI."""

from pathlib import Path

import cappa

from vid_cleaner.constants import VideoContainerTypes
from vid_cleaner.models.video_file import VideoFile


def coerce_video_files(files: list[Path]) -> list[VideoFile]:
    """Parse and validate a list of video file paths.

    Verify each path exists and has a valid video container extension. Convert valid paths into VideoFile objects.

    Args:
        files (list[Path]): List of file paths to validate and convert

    Returns:
        list[VideoFile]: List of validated VideoFile objects

    Raises:
        cappa.Exit: If a file doesn't exist or has an invalid extension
    """
    for file in files:
        f = file.expanduser().resolve().absolute()

        if not f.is_file():
            msg = f"File '{file}' does not exist"
            raise cappa.Exit(msg, code=1)

        if f.suffix.lower() not in [container.value for container in VideoContainerTypes]:
            msg = f"File {file} is not a video file"
            raise cappa.Exit(msg, code=1)

    return [VideoFile(path.expanduser().resolve().absolute()) for path in files]
