"""Utilities for CLI."""

import shutil
from pathlib import Path

import cappa
from nclutils import pp
from nclutils.fs import backup_path, copy_file

from vid_cleaner.constants import (
    DEFAULT_CONFIG_PATH,
    SYMBOL_CHECK,
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


def copy_to_output(src: Path, dst: Path, *, overwrite: bool) -> tuple[Path, list[str]]:
    """Copy a processed file to its destination, backing up any existing file first.

    Own the backup explicitly rather than letting ``copy_file`` make a silent one, so the backup's location can be reported back to the user.

    Args:
        src (Path): The processed temporary file to copy.
        dst (Path): The destination path to write to.
        overwrite (bool): When True, replace the destination without keeping a backup.

    Returns:
        tuple[Path, list[str]]: The written file and substep messages describing the backup (if any) and the save.
    """
    # Resolve up front so the backup and save messages report the same canonical path.
    dst = dst.expanduser().resolve()
    messages: list[str] = []

    if not overwrite and dst.exists():
        backup = backup_path(dst, with_progress=True, transient=True, console=pp.console())
        if backup:
            messages.append(f"{SYMBOL_CHECK} Backed up original to {backup}")

    out_file = copy_file(
        src=src,
        dst=dst,
        keep_backup=False,  # already handled above so the backup path can be surfaced
        with_progress=True,
        transient=True,
        console=pp.console(),
    )
    messages.append(f"{SYMBOL_CHECK} Saved to {out_file}")

    return out_file, messages


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
