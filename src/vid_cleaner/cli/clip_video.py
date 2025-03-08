"""Clip subcommand."""

import re

import cappa

from vid_cleaner.constants import PrintLevel
from vid_cleaner.utils import coerce_video_files, pp, settings, tmp_to_output
from vid_cleaner.vidcleaner import ClipCommand, VidCleaner


def main(cmd: VidCleaner, clip_cmd: ClipCommand) -> None:
    """Extract video clips based on start time and duration.

    Create video clips by copying a section of the source video without re-encoding. Useful for extracting highlights or samples from longer videos.

    Args:
        cmd (VidCleaner): Global command options and configuration
        clip_cmd (ClipCommand): Clip-specific command options

    Raises:
        cappa.Exit: If start or duration times are not in HH:MM:SS format
    """
    settings.update(
        {
            "dryrun": cmd.dry_run,
            "out_path": clip_cmd.out,
            "overwrite": clip_cmd.overwrite,
        },
    )
    pp.configure(
        debug=cmd.verbosity in {PrintLevel.DEBUG, PrintLevel.TRACE},
        trace=cmd.verbosity == PrintLevel.TRACE,
    )

    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(clip_cmd.start):
        pp.error("`--start` must be in format HH:MM:SS")  # type: ignore [unreachable]
        raise cappa.Exit(code=1)

    if not time_pattern.match(clip_cmd.duration):
        pp.error("`--duration` must be in format HH:MM:SS")  # type: ignore [unreachable]
        raise cappa.Exit(code=1)

    for video in coerce_video_files(clip_cmd.files):
        pp.info(f"⇨ {video.path.name}")

        video.clip(clip_cmd.start, clip_cmd.duration)

        if not cmd.dry_run:
            out_file = tmp_to_output(
                video.temp_file.latest_temp_path(),
                stem=video.stem,
                new_file=clip_cmd.out,
                overwrite=clip_cmd.overwrite,
            )
            video.cleanup()
            pp.success(f"{out_file}")

    raise cappa.Exit(code=0)
