"""Utilities for CLI."""

import shutil
from pathlib import Path

import cappa

from vid_cleaner.constants import DEFAULT_CONFIG_PATH, USER_CONFIG_PATH, VideoContainerTypes
from vid_cleaner.models.video_file import VideoFile

from .printer import pp


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


def create_default_config() -> None:
    """Create a default configuration file.

    Create a new configuration file at the default user location if one does not already exist. Copy the default configuration template to initialize the file.
    """
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.touch(exist_ok=True)
        shutil.copy(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
        pp.info(f"Default configuration file created: `{USER_CONFIG_PATH}`")
