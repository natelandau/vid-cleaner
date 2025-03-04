"""Inspect command."""

import typer

from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.utils import console


def inspect(files: list[VideoFile], json_output: bool = False) -> None:
    """Inspect a list of video files and output their metadata details.

    Iterates over a list of video files, using `ffprobe` to inspect each file. Depending on the `json_output` flag, the function either prints a JSON representation of the video file details or a formatted table of the video streams. The function exits the program after printing the details of all video files.

    Args:
        files: A list of `VideoFile` objects to inspect.
        json_output: A boolean flag. If True, output the details in JSON format. Defaults to False.

    Raises:
        typer.Exit: Exits the program after printing the details of all video files.
    """
    for video in files:
        if json_output:
            console.print(video.ffprobe_json())
            continue

        console.print(video.as_stream_table())

    raise typer.Exit()
