"""Utilities for CLI."""

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import cappa
from nclutils import pp
from nclutils.fs import copy_file

from vid_cleaner import settings
from vid_cleaner.constants import (
    DEFAULT_CONFIG_PATH,
    SYMBOL_CHECK,
    USER_CONFIG_PATH,
    VideoContainerTypes,
    VideoTrait,
)

if TYPE_CHECKING:
    from vid_cleaner.models.video_file import VideoFile


def coerce_video_files(files: list[Path]) -> list["VideoFile"]:
    """Parse and validate a list of video file paths.

    Verify each path exists and has a valid video container extension. Convert valid paths into VideoFile objects.

    Args:
        files (list[Path]): List of file paths to validate and convert

    Returns:
        list[VideoFile]: List of validated VideoFile objects

    Raises:
        cappa.Exit: If a file doesn't exist or has an invalid extension
    """
    # Imported here because `vid_cleaner.models.video_file` imports this package at module
    # scope; a top-level import makes `vid_cleaner.models` unimportable on its own.
    from vid_cleaner.models.video_file import VideoFile  # noqa: PLC0415

    for file in files:
        f = file.expanduser().resolve().absolute()

        if not f.is_file():
            msg = f"File '{file}' does not exist"
            raise cappa.Exit(msg, code=1)

        if f.suffix.lower() not in [container.value for container in VideoContainerTypes]:
            msg = f"File {file} is not a video file"
            raise cappa.Exit(msg, code=1)

    return [VideoFile(path.expanduser().resolve().absolute()) for path in files]


def resolve_out_path_override(
    files: list[Path], *, from_directory: Path | None = None
) -> Path | None:
    """Capture the explicit `--out` override before per-file processing mutates it.

    Read `settings.out_path` once up front so the first file's resolved path can't leak into later files and overwrite them all with the first file's name. Reject a single `--out` paired with a multi-file selection, since one path cannot name many outputs.

    Args:
        files (list[Path]): The input files the command will process.
        from_directory (Path | None): The `--from` discovery root, when discovery mode is active.

    Returns:
        Path | None: The user's explicit `--out` value, or None when not set.

    Raises:
        cappa.Exit: If `--out` is combined with more than one input file or with `--from`.
    """
    override = settings.out_path
    if override and from_directory is not None:
        pp.error("`--out` cannot be used with `--from`")
        raise cappa.Exit(code=1)
    if override and len(files) > 1:
        pp.error("`--out` cannot be used with multiple input files")
        raise cappa.Exit(code=1)
    return override


def copy_to_output(src: Path, dst: Path, *, overwrite: bool) -> tuple[Path, list[str]]:
    """Copy a processed file to its destination via an atomic same-filesystem rename.

    Stage the copy beside ``dst`` and swap it into place with ``Path.replace`` so a
    crash or I/O error mid-copy leaves the original intact rather than truncated. Own
    the backup explicitly so its location can be reported back to the user.

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

    # Stage beside dst (same filesystem) so the final swap is an atomic rename.
    # copy_file verifies the copied size, so a short or failed copy raises here,
    # before the original is ever touched. Keep the temp name a fixed length
    # (not dst.name + suffix) so a long-but-valid destination name can't push
    # the staged path past the filesystem's 255-byte component limit.
    staged = dst.with_name(f".vidcleaner-tmp-{uuid.uuid4().hex}{dst.suffix}")
    backup: Path | None = None
    try:
        copy_file(
            src=src,
            dst=staged,
            keep_backup=False,
            with_progress=True,
            transient=True,
            console=pp.console(),
        )

        # Move (not copy) the original aside so peak destination disk stays at 2x,
        # then free the slot for the atomic swap below.
        if dst.exists() and not overwrite:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            backup = dst.with_name(f"{dst.name}.{stamp}-{uuid.uuid4().hex[:8]}.bak")
            dst.replace(backup)
            messages.append(f"{SYMBOL_CHECK} Backed up original to {backup}")

        try:
            staged.replace(dst)  # atomic within the destination filesystem
        except OSError:
            # A same-filesystem rename failing here is near-impossible, but if it does
            # after the original was moved aside, put the original back at its expected
            # path rather than stranding it under the .bak name.
            if backup is not None:
                backup.replace(dst)
            raise
    finally:
        # No-op on success (staged was renamed away); removes the partial temp on failure.
        staged.unlink(missing_ok=True)

    messages.append(f"{SYMBOL_CHECK} Saved to {dst}")
    return dst, messages


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
