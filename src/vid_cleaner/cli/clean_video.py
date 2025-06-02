"""Clean subcommand."""

from pathlib import Path

import cappa
from nclutils import copy_file, pp

from vid_cleaner import settings
from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import CleanCommand

from vid_cleaner.models.video_file import VideoFile  # isort: skip


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

        pp.info(f"⇨ {video.path.name}")
        video.reorder_streams()
        video.process_streams()

        if not settings.dryrun and settings.save_each_step:
            out_file = copy_file(
                src=video.temp_file.latest_temp_path(),
                dst=settings.out_path,
                keep_backup=not settings.overwrite,
                with_progress=True,
                transient=True,
            )
            pp.success(f"{out_file}")
            video.temp_file.clean_up()

            video = VideoFile(Path(out_file))  # noqa: PLW2901

        if settings.video_1080:
            video.video_to_1080p()

            if not settings.dryrun and settings.save_each_step:
                out_file = copy_file(
                    src=video.temp_file.latest_temp_path(),
                    dst=settings.out_path,
                    keep_backup=not settings.overwrite,
                    with_progress=True,
                    transient=True,
                )
                pp.success(f"{out_file}")
                video.temp_file.clean_up()

                video = VideoFile(Path(out_file))  # noqa: PLW2901

        if settings.h265:
            video.convert_to_h265()

        if settings.vp9:
            video.convert_to_vp9()

        if not settings.dryrun:
            out_file = copy_file(
                src=video.temp_file.latest_temp_path(),
                dst=settings.out_path,
                keep_backup=not settings.overwrite,
                with_progress=True,
                transient=True,
            )
            video.temp_file.clean_up()

            if settings.overwrite and out_file != video.path:
                pp.debug(f"Delete: {video.path}")
                video.path.unlink()

            pp.success(f"{out_file}")

    raise cappa.Exit(code=0)
