"""Inspect subcommand."""

import cappa
from nclutils import console

from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import InspectCommand
from vid_cleaner.views import stream_table


def main(inspect_cmd: InspectCommand) -> None:
    """Inspect video files.

    Inspect video files and print detailed information about them.

    Args:
        cmd (VidCleaner): The main command instance containing global options
        inspect_cmd (InspectCommand): The inspect subcommand instance with inspect-specific options

    Raises:
        cappa.Exit: If the video files are not found or if the inspect command is not valid
    """
    for video in coerce_video_files(inspect_cmd.files):
        if inspect_cmd.json_output:
            console.print(video.ffprobe_json())
            continue

        console.print(stream_table(video.probe_box))

    raise cappa.Exit(code=0)
