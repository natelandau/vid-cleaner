"""Clean subcommand."""

from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner import settings
from vid_cleaner.utils import coerce_video_files, copy_to_output, render_substeps
from vid_cleaner.vidcleaner import CleanCommand

from vid_cleaner.models.video_file import VideoFile  # isort: skip


def save_each_step(video: VideoFile) -> tuple[VideoFile, list[str]]:
    """Save the intermediate result of the current processing step.

    Args:
        video (VideoFile): The video file to save

    Returns:
        tuple[VideoFile, list[str]]: The (possibly new) video file and substep messages describing the save, empty when nothing was saved.
    """
    if not settings.dryrun and settings.save_each_step:
        out_file, messages = copy_to_output(
            video.temp_file.latest_temp_path(),
            Path(settings.out_path),
            overwrite=settings.overwrite,
        )
        video.temp_file.clean_up()

        return VideoFile(Path(out_file)), messages

    return video, []


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

    Apply video processing operations like stream reordering, audio/subtitle filtering, and format conversion based on command line arguments.

    Args:
        cmd (VidCleaner): Global command options and configuration
        clean_cmd (CleanCommand): Clean-specific command options

    Raises:
        cappa.Exit: If incompatible options are specified (e.g., both H265 and VP9)
    """
    if settings.h265 and settings.vp9:
        pp.error("Cannot convert to both H265 and VP9")
        raise cappa.Exit(code=1)

    for video in coerce_video_files(clean_cmd.files):
        settings.out_path = settings.out_path or video.path

        video_file = video

        # Print the video name first so live progress bars render beneath it, then collect each
        # operation's outcome and render the result tree once the file is done. The render runs in
        # `finally` so completed steps are still shown if a later operation raises.
        pp.info(f"⇨ {video_file.path.name}")
        substeps: list[str] = []

        try:
            substeps.extend(video_file.reorder_streams())
            substeps.extend(video_file.process_streams())
            video_file, saved = save_each_step(video_file)
            substeps.extend(saved)

            if settings.video_1080:
                substeps.extend(video_file.video_to_1080p())
                video_file, saved = save_each_step(video_file)
                substeps.extend(saved)

            if settings.h265:
                substeps.extend(video_file.convert_to_h265())

            if settings.vp9:
                substeps.extend(video_file.convert_to_vp9())

            if not settings.dryrun:
                substeps.extend(write_output(video_file))
        finally:
            render_substeps(substeps)

    raise cappa.Exit(code=0)
