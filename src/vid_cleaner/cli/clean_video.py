"""Clean subcommand."""

from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner import settings
from vid_cleaner.cli.discovery_output import confirm_selection, present_discovery
from vid_cleaner.constants import SYMBOL_CROSS, SortOrder
from vid_cleaner.controllers.discovery import discover_video_files
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    coerce_video_files,
    copy_to_output,
    render_substeps,
    resolve_out_path_override,
)
from vid_cleaner.vidcleaner import CleanCommand

from vid_cleaner.models.video_file import VideoFile  # isort: skip


def write_output(video_file: VideoFile) -> list[str]:
    """Copy the processed result to the output path and return the closing substep messages.

    Args:
        video_file (VideoFile): The processed video file to write out.

    Returns:
        list[str]: Substep messages describing the backup and save, or a single note when nothing changed.
    """
    if video_file.temp_file.latest_temp_path() == video_file.path:
        return [f"No changes made: `{video_file.name}`"]

    out_file, messages = copy_to_output(
        video_file.temp_file.latest_temp_path(),
        Path(settings.out_path),
        overwrite=settings.overwrite,
    )

    # The write above already landed and swapped the result into place, so a failure past
    # this point is temp-directory housekeeping, not a failed write: warn instead of
    # discarding the "Saved to" message and counting a successful file as failed.
    try:
        video_file.temp_file.clean_up()
        if settings.overwrite and out_file != video_file.path:
            pp.debug(f"Delete: {video_file.path}")
            video_file.path.unlink()
    except OSError as e:
        messages.append(f"{SYMBOL_CROSS} Warning: could not clean up temporary files: {e}")

    return messages


def select_video_files(clean_cmd: CleanCommand) -> list[VideoFile]:
    """Resolve the files to clean from either explicit paths or a `--from` discovery run.

    Keep the two modes strictly separate so a query and a path list can never disagree
    about what the command is about to rewrite.

    Args:
        clean_cmd (CleanCommand): The parsed clean command.

    Returns:
        list[VideoFile]: The files to process, in the order they should be processed.

    Raises:
        cappa.Exit: If the two modes are mixed, if discovery flags appear without
            `--from`, if neither a file nor `--from` was given, or if `--from` does not
            name an existing directory.
    """
    discovery = clean_cmd.discovery
    # Compare against defaults rather than asking cappa what the user typed; an explicit
    # `--sort=alpha` would have been a no-op anyway, so treating it as unset is harmless.
    uses_discovery_flags = bool(
        discovery.filters
        or discovery.limit is not None
        or discovery.depth
        or discovery.reverse
        or discovery.sort != SortOrder.ALPHA
        or clean_cmd.yes
    )

    if clean_cmd.from_ is not None and clean_cmd.files:
        pp.error("`--from` cannot be combined with explicit file paths")
        raise cappa.Exit(code=1)

    if clean_cmd.from_ is None and uses_discovery_flags:
        pp.error(
            "These options require `--from`: `--filters`, `--sort`, `--reverse`, `--depth`, `--limit`, `--yes`"
        )
        raise cappa.Exit(code=1)

    if clean_cmd.from_ is None:
        if not clean_cmd.files:
            pp.error("Provide file path(s) or `--from` to discover them")
            raise cappa.Exit(code=1)
        return coerce_video_files(clean_cmd.files)

    # A missing or non-directory `--from` would otherwise discover nothing and exit 0, so a
    # typo or an unmounted media share would report success having cleaned nothing.
    if not clean_cmd.from_.is_dir():
        pp.error(f"`--from` must be an existing directory: {clean_cmd.from_}")
        raise cappa.Exit(code=1)

    filters = set(settings.filters)
    report = discover_video_files(
        clean_cmd.from_,
        depth=discovery.depth,
        filters=filters,
        sort=discovery.sort,
        reverse=discovery.reverse,
        limit=discovery.limit,
    )

    present_discovery(
        report,
        root=clean_cmd.from_,
        filters=filters,
        recursive=discovery.depth > 0,
    )

    # A dry run previews rather than acts, so there is nothing to approve.
    if not settings.dryrun:
        confirm_selection(report, assume_yes=clean_cmd.yes)

    # Discovery already probed every file and `VideoFile` caches its probe, so reusing
    # these instances skips a second ffprobe per file.
    return [result.video_file for result in report.results]


def main(clean_cmd: CleanCommand) -> None:
    """Process video files according to specified cleaning options.

    Compose stream reordering, audio/subtitle filtering, downmixing, scaling, and codec
    conversion into a single ffmpeg pass per file.

    Args:
        clean_cmd (CleanCommand): Clean-specific command options

    Raises:
        cappa.Exit: If incompatible options are specified (e.g., both H265 and VP9), or
            if one or more files failed to process.
    """
    if settings.h265 and settings.vp9:
        pp.error("Cannot convert to both H265 and VP9")
        raise cappa.Exit(code=1)

    # Check the `--out`/`--from` conflict before discovery runs, so an empty `--from`
    # directory can't short-circuit the command with "no files found" ahead of this error.
    out_path_override = resolve_out_path_override(clean_cmd.files, from_directory=clean_cmd.from_)
    video_files = select_video_files(clean_cmd)

    failures: list[str] = []

    for video_file in video_files:
        settings.out_path = out_path_override or video_file.path

        # Print the video name first so live progress bars and clean()'s up-front operation
        # tree render beneath it. Only the write-output messages are collected here and
        # rendered in `finally`, so they still show if `write_output()` raises.
        pp.info(f"⇨ {video_file.path.name}")
        substeps: list[str] = []

        try:
            video_file.clean()
            if not settings.dryrun:
                substeps.extend(write_output(video_file))
        # One unusable or failing file must not discard the files queued behind it.
        # `cappa.Exit` is deliberately not caught: it carries KeyboardInterrupt, which
        # means stop the whole run.
        except (VideoCleanError, VideoProbeError, RuntimeError, OSError) as e:
            # VideoCleanError/VideoProbeError's str() already embeds the path, so use the
            # short reason instead to avoid naming the file twice in one line.
            detail = e.reason if isinstance(e, (VideoCleanError, VideoProbeError)) else str(e)
            failures.append(f"{video_file.path.name}: {detail}")
            substeps.append(f"{SYMBOL_CROSS} Failed: {detail}")
            # A failed file still leaves its full-size temp transcode on disk; clear it now
            # rather than letting a batch that fails on every file pile up N of them until
            # atexit finally clears them.
            try:
                video_file.temp_file.clean_up()
            except OSError as cleanup_error:
                pp.debug(
                    f"Could not clean up temporary files for {video_file.path}: {cleanup_error}"
                )
        finally:
            render_substeps(substeps)

    if failures:
        pp.error(f"{len(failures)} file(s) failed", details=failures, markup=False)
        raise cappa.Exit(code=1)

    raise cappa.Exit(code=0)
