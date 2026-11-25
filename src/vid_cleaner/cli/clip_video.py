"""Clip subcommand."""

import re
from typing import Annotated

import cappa
from cappa import Dep
from nclutils import pp

from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import SYMBOL_CROSS
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    TEMP_CLEANUP_WARNING,
    coerce_video_files,
    output_write_messages,
    render_substeps,
    resolve_out_path_override,
)
from vid_cleaner.vidcleaner import ClipCommand, build_config, build_run_options

from vid_cleaner.cli.ffmpeg_step import (  # isort: skip
    copy_result_to_output,
    run_ffmpeg_step,
    run_verify_step,
    verification_enabled,
    verified_message,
)
from vid_cleaner.controllers.validation import expected_clip_output  # isort: skip

TIME_FORMAT_ERROR = "must be in format HH:MM:SS"


def main(
    clip_cmd: ClipCommand,
    config: Annotated[Config, Dep(build_config)],
    options: Annotated[RunOptions, Dep(build_run_options)],
) -> None:
    """Extract video clips based on start time and duration.

    Create video clips by copying a section of the source video without re-encoding. Useful for extracting highlights or samples from longer videos.

    Args:
        clip_cmd (ClipCommand): Clip-specific command options
        config (Config): The settings for this run
        options (RunOptions): The CLI-only values for this run

    Raises:
        cappa.Exit: If start or duration times are not in HH:MM:SS format, or if one or
            more files failed to clip
    """
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(clip_cmd.start):
        pp.error(f"`--start` {TIME_FORMAT_ERROR}")
        raise cappa.Exit(code=1)

    if not time_pattern.match(clip_cmd.duration):
        pp.error(f"`--duration` {TIME_FORMAT_ERROR}")
        raise cappa.Exit(code=1)

    out_path_override = resolve_out_path_override(clip_cmd.files, out=options.out)

    failures: list[str] = []

    for video in coerce_video_files(clip_cmd.files):
        # Print the video name first so live progress bars render beneath it, then collect each
        # operation's outcome and render the result tree once the file is done. The render runs in
        # `finally` so completed steps are still shown if a later operation raises.
        pp.info(f"⇨ {video.path.name}")
        substeps: list[str] = []

        try:
            # Built before ffmpeg runs: once a temp file exists, the source probes that instead
            expected = (
                None
                if not verification_enabled(config=config, options=options)
                else expected_clip_output(video, start=clip_cmd.start, duration=clip_cmd.duration)
            )
            command = video.prepare_ffmpeg(
                video.clip_arguments(start=clip_cmd.start, duration=clip_cmd.duration),
                step="clip",
                config=config,
                dryrun=options.dryrun,
            )
            substeps.extend(
                run_ffmpeg_step(video, command, title="Clip video", dryrun=options.dryrun)
            )

            if not options.dryrun:
                verified_level = run_verify_step(video, expected=expected, level=config.verify)
                if verified_level is not None:
                    substeps.append(verified_message(verified_level))
                write = copy_result_to_output(
                    video, out_path_override or video.path, overwrite=config.overwrite
                )
                # The clip is already written, so keep its messages and treat a cleanup
                # failure as a warning rather than losing the save and raising past main().
                substeps.extend(output_write_messages(write))
                try:
                    video.temp_file.clean_up()
                except OSError as e:
                    substeps.append(f"{SYMBOL_CROSS} {TEMP_CLEANUP_WARNING}: {e}")
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
                substeps.append(f"{SYMBOL_CROSS} {TEMP_CLEANUP_WARNING}: {cleanup_error}")
        finally:
            render_substeps(substeps)

    if failures:
        pp.error("Failed to clip:", details=failures)
        raise cappa.Exit(code=1)

    raise cappa.Exit(code=0)
