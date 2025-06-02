"""Clip subcommand."""

import re

import cappa
from nclutils import copy_file, pp

from vid_cleaner import settings
from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import ClipCommand


def main(clip_cmd: ClipCommand) -> None:
    """Extract video clips based on start time and duration.

    Create video clips by copying a section of the source video without re-encoding. Useful for extracting highlights or samples from longer videos.

    Args:
        cmd (VidCleaner): Global command options and configuration
        clip_cmd (ClipCommand): Clip-specific command options

    Raises:
        cappa.Exit: If start or duration times are not in HH:MM:SS format
    """
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(clip_cmd.start):
        pp.error("`--start` must be in format HH:MM:SS")  # type: ignore [unreachable]
        raise cappa.Exit(code=1)

    if not time_pattern.match(clip_cmd.duration):
        pp.error("`--duration` must be in format HH:MM:SS")  # type: ignore [unreachable]
        raise cappa.Exit(code=1)

    for video in coerce_video_files(clip_cmd.files):
        settings.out_path = settings.out_path or video.path
        pp.info(f"⇨ {video.path.name}")

        video.clip(clip_cmd.start, clip_cmd.duration)

        if not settings.dryrun:
            out_file = copy_file(
                src=video.temp_file.latest_temp_path(),
                dst=settings.out_path,
                keep_backup=not settings.overwrite,
                with_progress=True,
                transient=True,
            )
            video.temp_file.clean_up()
            pp.success(f"{out_file}")

    raise cappa.Exit(code=0)
