"""Clip command for vid_cleaner."""

import re
from pathlib import Path

import typer
from loguru import logger

from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.utils import tmp_to_output


def clip(
    files: list[VideoFile], start: str, duration: str, out: Path, overwrite: bool, dry_run: bool
) -> None:
    """Clips video files based on the specified start time and duration, saving the output in a given directory.

    This function processes each video in the provided list, clipping it according to the specified start time and duration. The resulting clips are saved in the specified output directory. The function supports overwriting existing files and performing a dry run, where no actual clipping occurs.

    Args:
        files: A list of VideoFile objects to be clipped.
        start: The start time for the clip in HH:MM:SS format.
        duration: The duration of the clip in HH:MM:SS format.
        out: The output directory Path where the clipped files will be saved.
        overwrite: A boolean indicating if existing files should be overwritten.
        dry_run: A boolean indicating if the clip operation should be simulated (no actual clipping).

    Raises:
        typer.BadParameter: If either 'start' or 'duration' does not match the expected HH:MM:SS time format.
    """
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(start):
        msg = "Start must be in format HH:MM:SS"  # type: ignore [unreachable]
        raise typer.BadParameter(msg)

    if not time_pattern.match(duration):
        msg = "Duration must be in format HH:MM:SS"  # type: ignore [unreachable]
        raise typer.BadParameter(msg)

    for video in files:
        logger.info(f"⇨ {video.path.name}")

        video.clip(start, duration, dry_run=dry_run)

        if not dry_run:
            out_file = tmp_to_output(
                video.current_tmp_file, stem=video.stem, new_file=out, overwrite=overwrite
            )
            video.cleanup()
            logger.success(f"{out_file}")

    raise typer.Exit()
