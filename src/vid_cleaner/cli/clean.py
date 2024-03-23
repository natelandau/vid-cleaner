"""Clean command."""

from pathlib import Path

import typer
from loguru import logger

from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.utils import tmp_to_output


def clean(
    files: list[VideoFile],
    out: Path,
    replace: bool,
    downmix_stereo: bool,
    drop_original_audio: bool,
    keep_all_subtitles: bool,
    keep_commentary: bool,
    keep_local_subtitles: bool,
    subs_drop_local: bool,
    langs: str | None,
    h265: bool,
    vp9: bool,
    video_1080: bool,
    force: bool,
    dry_run: bool,
    verbosity: int,
) -> None:
    """Processes a list of video files with various cleaning and conversion options.

    This function performs a series of operations on video files, including reordering streams, processing audio and subtitle streams according to specified preferences, and converting the video to specified formats and resolutions. It can optionally perform these operations as a dry run, which does not apply any changes. If not a dry run, it handles output files according to specified replacement and verbosity settings.

    Args:
        files: A list of VideoFile objects to process.
        out: A Path object representing the output directory for processed files.
        replace: If True, replace the original files with the processed ones.
        downmix_stereo: If True, downmix audio to stereo.
        drop_original_audio: If True, drop the original audio streams from the file.
        keep_all_subtitles: If True, keep all subtitle streams.
        keep_commentary: If True, keep commentary audio streams.
        keep_local_subtitles: If True, keep subtitle streams in the local language.
        subs_drop_local: If True, drop local language subtitle streams.
        langs: A comma-separated string of language codes to keep in the processed video. None keeps default languages.
        h265: If True, convert video to H.265 codec.
        vp9: If True, convert video to VP9 codec.
        video_1080: If True, convert video to 1080p resolution.
        force: If True, force conversion even if it might result in loss of quality.
        dry_run: If True, perform a trial run without making any changes.
        verbosity: An integer that sets the verbosity level of the operation's output.

    Raises:
        typer.BadParameter: If both h265 and vp9 flags are set to True.
    """
    if h265 and vp9:
        msg = "Cannot convert to both H265 and VP9"
        raise typer.BadParameter(msg)

    languages = langs or ",".join(VidCleanerConfig().keep_languages)

    for video in files:
        logger.info(f"⇨ {video.path.name}")

        video.reorder_streams(dry_run=dry_run)

        video.process_streams(
            langs_to_keep=languages.split(","),
            drop_original_audio=drop_original_audio,
            keep_commentary=keep_commentary,
            downmix_stereo=downmix_stereo,
            keep_all_subtitles=keep_all_subtitles,
            keep_local_subtitles=keep_local_subtitles,
            subs_drop_local=subs_drop_local,
            dry_run=dry_run,
            verbosity=verbosity,
        )

        if video_1080:
            video.video_to_1080p(force=force, dry_run=dry_run)

        if h265:
            video.convert_to_h265(force=force, dry_run=dry_run)

        if vp9:
            video.convert_to_vp9(force=force, dry_run=dry_run)

        if not dry_run:
            out_file = tmp_to_output(
                video.current_tmp_file, stem=video.stem, new_file=out, overwrite=replace
            )
            video.cleanup()

            if replace and out_file != video.path:
                logger.debug(f"Delete: {video.path}")
                video.path.unlink()

            logger.success(f"{out_file}")

    raise typer.Exit()
