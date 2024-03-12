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
    """Clean command."""
    languages = langs or ",".join(VidCleanerConfig().keep_languages)

    for video in files:
        logger.info(f"⇨ {video.path.name}")

        if h265 and vp9:
            msg = "Cannot convert to both H265 and VP9"
            raise typer.BadParameter(msg)

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
            video._convert_to_h265(force=force, dry_run=dry_run)

        if vp9:
            video._convert_to_vp9(force=force, dry_run=dry_run)

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
