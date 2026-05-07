"""Utilities for CLI."""

import shutil
from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner.constants import (
    DEFAULT_CONFIG_PATH,
    USER_CONFIG_PATH,
    VideoContainerTypes,
    VideoTrait,
)
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


def create_default_config() -> None:
    """Create a default configuration file.

    Create a new configuration file at the default user location if one does not already exist. Copy the default configuration template to initialize the file.
    """
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.touch(exist_ok=True)
        shutil.copy(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
        pp.info(f"Default configuration file created: `{USER_CONFIG_PATH}`")


def parse_trait_filters(facets: str) -> set[VideoTrait]:
    """Parse a comma-separated list of facets into a list of VideoTrait enums.

    Args:
        facets (str): Comma-separated string of facet names to parse

    Returns:
        set[SearchFacet]: Set of VideoTrait enum values

    Raises:
        cappa.Exit: If any facet is invalid, exits with code 1
    """
    try:
        return {VideoTrait(facet.lower()) for facet in facets.split(",")}
    except ValueError as e:
        pp.error(f"Invalid facet: {e}")
        raise cappa.Exit(code=1) from e
