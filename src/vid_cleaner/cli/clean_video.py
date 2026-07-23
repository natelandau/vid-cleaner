"""Clean subcommand."""

from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner import settings
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
    video_file.temp_file.clean_up()

    if settings.overwrite and out_file != video_file.path:
        pp.debug(f"Delete: {video_file.path}")
        video_file.path.unlink()

    return messages


def main(clean_cmd: CleanCommand) -> None:
    """Process video files according to specified cleaning options.

    Compose stream reordering, audio/subtitle filtering, downmixing, scaling, and codec
    conversion into a single ffmpeg pass per file.

    Args:
        clean_cmd (CleanCommand): Clean-specific command options

    Raises:
        cappa.Exit: If incompatible options are specified (e.g., both H265 and VP9)
    """
    if settings.h265 and settings.vp9:
        pp.error("Cannot convert to both H265 and VP9")
        raise cappa.Exit(code=1)

    out_path_override = resolve_out_path_override(clean_cmd.files)

    for video_file in coerce_video_files(clean_cmd.files):
        settings.out_path = out_path_override or video_file.path

        # Print the video name first so live progress bars render beneath it, then collect the
        # run's outcome and render the result tree once the file is done. The render runs in
        # `finally` so completed steps are still shown if the operation raises.
        pp.info(f"⇨ {video_file.path.name}")
        substeps: list[str] = []

        try:
            substeps.extend(video_file.clean())
            if not settings.dryrun:
                substeps.extend(write_output(video_file))
        finally:
            render_substeps(substeps)

    raise cappa.Exit(code=0)
