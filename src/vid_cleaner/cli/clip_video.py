"""Clip subcommand."""

import re
from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner import settings
from vid_cleaner.constants import SYMBOL_CROSS
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    coerce_video_files,
    copy_to_output,
    render_substeps,
    resolve_out_path_override,
)
from vid_cleaner.vidcleaner import ClipCommand


def main(clip_cmd: ClipCommand) -> None:
    """Extract video clips based on start time and duration.

    Create video clips by copying a section of the source video without re-encoding. Useful for extracting highlights or samples from longer videos.

    Args:
        cmd (VidCleaner): Global command options and configuration
        clip_cmd (ClipCommand): Clip-specific command options

    Raises:
        cappa.Exit: If start or duration times are not in HH:MM:SS format, or if one or
            more files failed to clip
    """
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(clip_cmd.start):
        pp.error("`--start` must be in format HH:MM:SS")
        raise cappa.Exit(code=1)

    if not time_pattern.match(clip_cmd.duration):
        pp.error("`--duration` must be in format HH:MM:SS")
        raise cappa.Exit(code=1)

    out_path_override = resolve_out_path_override(clip_cmd.files)

    failures: list[str] = []

    for video in coerce_video_files(clip_cmd.files):
        settings.out_path = out_path_override or video.path

        # Print the video name first so live progress bars render beneath it, then collect each
        # operation's outcome and render the result tree once the file is done. The render runs in
        # `finally` so completed steps are still shown if a later operation raises.
        pp.info(f"⇨ {video.path.name}")
        substeps: list[str] = []

        try:
            substeps.extend(video.clip(clip_cmd.start, clip_cmd.duration))

            if not settings.dryrun:
                _, messages = copy_to_output(
                    video.temp_file.latest_temp_path(),
                    Path(settings.out_path),
                    overwrite=settings.overwrite,
                )
                # The clip is already written, so keep its messages and treat a cleanup
                # failure as a warning rather than losing the save and raising past main().
                substeps.extend(messages)
                try:
                    video.temp_file.clean_up()
                except OSError as e:
                    substeps.append(
                        f"{SYMBOL_CROSS} Warning: could not clean up temporary files: {e}"
                    )
        # One unusable or failing file must not discard the files queued behind it.
        # `cappa.Exit` is deliberately not caught: it carries KeyboardInterrupt, which
        # means stop the whole run.
        except (VideoCleanError, VideoProbeError, RuntimeError, OSError) as e:
            # VideoCleanError/VideoProbeError's str() already embeds the path, so use the
            # short reason instead to avoid naming the file twice in one line.
            detail = e.reason if isinstance(e, (VideoCleanError, VideoProbeError)) else str(e)
            failures.append(f"{video.path.name}: {detail}")
            substeps.append(f"{SYMBOL_CROSS} Failed: {detail}")
            # A failed file still leaves its temp copy on disk; clear it now rather than
            # letting a batch that fails on every file pile up N of them until atexit runs.
            try:
                video.temp_file.clean_up()
            except OSError as cleanup_error:
                substeps.append(
                    f"{SYMBOL_CROSS} Warning: could not clean up temporary files: {cleanup_error}"
                )
        finally:
            render_substeps(substeps)

    if failures:
        pp.error("Failed to clip:", details=failures)
        raise cappa.Exit(code=1)

    raise cappa.Exit(code=0)
