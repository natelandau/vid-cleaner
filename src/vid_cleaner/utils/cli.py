"""Utilities for CLI."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import cappa
from nclutils import pp
from nclutils.fs import copy_file
from nclutils.sh import which

from vid_cleaner import constants
from vid_cleaner.constants import (
    REQUIRED_BINARIES,
    SYMBOL_CHECK,
    VIDEO_SUFFIXES,
    VideoTrait,
)
from vid_cleaner.utils.console import stop_run

if TYPE_CHECKING:
    from vid_cleaner.models.video_file import VideoFile

OUT_WITH_FROM_ERROR = "`--out` cannot be used with `--from`"
OUT_WITH_MULTIPLE_FILES_ERROR = "`--out` cannot be used with multiple input files"
BACKED_UP_ORIGINAL_MESSAGE = "Backed up original to"
SAVED_TO_MESSAGE = "Saved to"
COUNT_BELOW_ONE_ERROR = "must be 1 or greater"
TEMP_CLEANUP_WARNING = "Warning: could not clean up temporary files"
ONLY_MACOS_SIDECARS_ERROR = (
    "No video files to process: every path named is a macOS `._` metadata file"
)


def is_macos_sidecar(path: Path) -> bool:
    """Report whether a path is a macOS AppleDouble `._` metadata file rather than a video.

    macOS writes one beside every file it copies to a non-HFS volume. It shares the
    video's extension but holds no video, so every way of naming files must skip it.
    Other dotfiles are not matched, because a user may deliberately hide a real video.

    Args:
        path (Path): The path to test. Only its name is read.

    Returns:
        bool: True when the file name starts with `._`.

    Examples:
        >>> is_macos_sidecar(Path("/share/._Movie.mkv"))
        True
        >>> is_macos_sidecar(Path("/share/.Movie.mkv"))
        False
    """
    return path.name.startswith("._")


def drop_macos_sidecars(paths: list[Path]) -> list[Path]:
    """Remove macOS `._` metadata files from paths named on the command line.

    A shell glob such as `/Volumes/share/*.mkv` matches the sidecars along with the
    videos, so they are skipped here the same way discovery skips them.

    Args:
        paths (list[Path]): The paths as the user named them.

    Returns:
        list[Path]: The paths that are not sidecars, in their original order.

    Raises:
        cappa.Exit: If paths were named and every one of them is a sidecar.
    """
    kept: list[Path] = []
    for path in paths:
        if is_macos_sidecar(path):
            pp.debug(f"Skip macOS metadata file: {path}")
        else:
            kept.append(path)

    if paths and not kept:
        raise cappa.Exit(ONLY_MACOS_SIDECARS_ERROR, code=1)
    return kept


def coerce_video_files(files: list[Path]) -> list["VideoFile"]:
    """Parse and validate a list of video file paths.

    Verify each path exists and has a valid video container extension. Convert valid paths into VideoFile objects.

    Args:
        files (list[Path]): List of file paths to validate and convert

    Returns:
        list[VideoFile]: List of validated VideoFile objects

    Raises:
        cappa.Exit: If a file doesn't exist or has an invalid extension, or every file is a
            macOS `._` metadata file
    """
    # Imported here because `vid_cleaner.models.video_file` imports this package at module
    # scope; a top-level import makes `vid_cleaner.models` unimportable on its own.
    from vid_cleaner.models.video_file import VideoFile  # noqa: PLC0415

    files = drop_macos_sidecars(files)
    for file in files:
        f = file.expanduser().resolve().absolute()

        if not f.is_file():
            msg = f"File '{file}' does not exist"
            raise cappa.Exit(msg, code=1)

        if f.suffix.lower() not in VIDEO_SUFFIXES:
            msg = f"File {file} is not a video file"
            raise cappa.Exit(msg, code=1)

    return [VideoFile(path.expanduser().resolve().absolute()) for path in files]


def resolve_out_path_override(
    files: list[Path], *, out: Path | None, from_directory: Path | None = None
) -> Path | None:
    """Validate the explicit `--out` override against the selection it would apply to.

    Reject a single `--out` paired with a multi-file selection, since one path cannot name many outputs.

    Args:
        files (list[Path]): The input files the command will process.
        out (Path | None): The `--out` value, or None when not given.
        from_directory (Path | None): The `--from` discovery root, when discovery mode is active.

    Returns:
        Path | None: The user's explicit `--out` value, or None when not set.

    Raises:
        RunStopped: If `--out` is combined with more than one input file or with `--from`.
    """
    override = out
    if override and from_directory is not None:
        raise stop_run(OUT_WITH_FROM_ERROR)
    # Count only what will be processed, so `--out` with a glob that also matched a
    # video's `._` sidecar still names a single output
    if override and len([file for file in files if not is_macos_sidecar(file)]) > 1:
        raise stop_run(OUT_WITH_MULTIPLE_FILES_ERROR)
    return override


@dataclass(frozen=True)
class OutputWrite:
    """Where a processed file was written, so callers can report it as text or as data.

    Attributes:
        path (Path): The written file, resolved.
        backup_path (Path | None): Where the replaced original was moved, or None when
            nothing was backed up.
    """

    path: Path
    backup_path: Path | None


def output_write_messages(write: OutputWrite) -> list[str]:
    """Describe a finished write as substep messages for the per-file result tree.

    Args:
        write (OutputWrite): The write to describe.

    Returns:
        list[str]: The backup message, when a backup was made, followed by the save message.
    """
    messages: list[str] = []
    if write.backup_path is not None:
        messages.append(f"{SYMBOL_CHECK} {BACKED_UP_ORIGINAL_MESSAGE} {write.backup_path}")
    messages.append(f"{SYMBOL_CHECK} {SAVED_TO_MESSAGE} {write.path}")
    return messages


def copy_to_output(src: Path, dst: Path, *, overwrite: bool) -> OutputWrite:
    """Copy a processed file to its destination via an atomic same-filesystem rename.

    Stage the copy beside ``dst`` and swap it into place with ``Path.replace`` so a
    crash or I/O error mid-copy leaves the original intact rather than truncated. Own
    the backup explicitly so its location can be reported back to the user.

    Args:
        src (Path): The processed temporary file to copy.
        dst (Path): The destination path to write to.
        overwrite (bool): When True, replace the destination without keeping a backup.

    Returns:
        OutputWrite: The written file and the backup of the replaced original, if any.
    """
    # Resolve up front so the backup and save messages report the same canonical path.
    dst = dst.expanduser().resolve()

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

        try:
            staged.replace(dst)  # atomic within the destination filesystem
        except BaseException:
            # A same-filesystem rename failing here is near-impossible, but a stop signal
            # can land between the two renames. Either way, once the original was moved
            # aside, put it back at its expected path rather than stranding it under the
            # .bak name.
            if backup is not None:
                backup.replace(dst)
            raise
    finally:
        # No-op on success (staged was renamed away); removes the partial temp on failure.
        staged.unlink(missing_ok=True)

    return OutputWrite(path=dst, backup_path=backup)


def find_missing_binaries() -> list[str]:
    """Report which of the external programs vid-cleaner needs are absent from PATH.

    Returns:
        list[str]: The absent program names, in the order they are required. Empty when
            every program is installed.
    """
    return [name for name in REQUIRED_BINARIES if which(name) is None]


def create_default_config() -> None:
    """Create the user configuration file from the default template when it is missing.

    Create the file readable only by its owner, because it can hold API keys.
    """
    user_config_path = constants.USER_CONFIG_PATH
    if user_config_path.exists():
        return
    user_config_path.parent.mkdir(parents=True, exist_ok=True)
    template = constants.DEFAULT_CONFIG_PATH.read_bytes()
    # Write beside the target with the final mode and rename into place, so the file is
    # never briefly readable by others and a failed write never leaves an empty config
    staged = user_config_path.with_name(f".{user_config_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        staged.touch(mode=0o600, exist_ok=False)
        staged.write_bytes(template)
        staged.replace(user_config_path)
    finally:
        staged.unlink(missing_ok=True)
    pp.info(f"Default configuration file created: `{user_config_path}`")


def parse_count(raw: str) -> int:
    """Parse a count flag such as `--limit` or `--jobs`, rejecting anything below one.

    Reject the bad value at parse time because a count reaches a list slice or a thread
    pool unchecked: zero selects nothing and a negative `--limit` quietly trims from the
    end, so either one acts on a selection the user never described.

    Args:
        raw (str): The raw command line value.

    Returns:
        int: The validated count.

    Raises:
        ValueError: If the value is not a whole number of 1 or greater.
    """
    value = int(raw)
    if value < 1:
        msg = f"{COUNT_BELOW_ONE_ERROR}, got {value}"
        raise ValueError(msg)
    return value


def parse_trait_filters(facets: str) -> set[VideoTrait]:
    """Parse a comma-separated list of facets into a set of VideoTrait enums.

    Raise `ValueError` rather than exiting so cappa reports the bad value as a usage
    error, the same way it does for every other discovery flag.

    Args:
        facets (str): Comma-separated string of facet names to parse

    Returns:
        set[VideoTrait]: Set of VideoTrait enum values

    Raises:
        ValueError: If any facet is not a known video trait.
    """
    traits: set[VideoTrait] = set()
    for facet in facets.split(","):
        try:
            traits.add(VideoTrait(facet.strip().lower()))
        except ValueError as e:
            msg = f"'{facet}' is not a valid filter. Valid options: {VideoTrait.help_options()}"
            raise ValueError(msg) from e
    return traits
