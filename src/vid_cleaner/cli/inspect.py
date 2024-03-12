"""Inspect command."""

import typer

from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.utils import console


def inspect(files: list[VideoFile], json_output: bool = False) -> None:
    """Inspect command."""
    for video in files:
        if json_output:
            console.print(video.ffprobe_json())
            continue

        console.print(video.as_stream_table())

    raise typer.Exit()
