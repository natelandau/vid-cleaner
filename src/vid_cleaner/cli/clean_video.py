"""Clean subcommand."""

import cappa

from vid_cleaner.constants import PrintLevel
from vid_cleaner.utils import coerce_video_files, pp, settings, tmp_to_output
from vid_cleaner.vidcleaner import CleanCommand, VidCleaner


def main(cmd: VidCleaner, clean_cmd: CleanCommand) -> None:
    """Process video files according to specified cleaning options.

    Apply video processing operations like stream reordering, audio/subtitle filtering, and format conversion based on command line arguments.

    Args:
        cmd (VidCleaner): Global command options and configuration
        clean_cmd (CleanCommand): Clean-specific command options

    Raises:
        cappa.Exit: If incompatible options are specified (e.g., both H265 and VP9)
    """
    settings.update(
        {
            "dryrun": cmd.dry_run,
            "langs_to_keep": clean_cmd.langs_to_keep.split(",")
            if clean_cmd.langs_to_keep
            else settings.langs_to_keep,
            "drop_local_subs": clean_cmd.drop_local_subs or settings.drop_local_subs,
            "keep_local_subtitles": clean_cmd.keep_local_subtitles or settings.keep_local_subtitles,
            "keep_commentary": clean_cmd.keep_commentary or settings.keep_commentary,
            "keep_all_subtitles": clean_cmd.keep_all_subtitles or settings.keep_all_subtitles,
            "drop_original_audio": clean_cmd.drop_original_audio or settings.drop_original_audio,
            "downmix_stereo": clean_cmd.downmix_stereo or settings.downmix_stereo,
            "overwrite": clean_cmd.overwrite,
            "out_path": clean_cmd.out,
            "h265": clean_cmd.h265,
            "vp9": clean_cmd.vp9,
            "video_1080": clean_cmd.video_1080,
            "force": clean_cmd.force,
        },
    )
    pp.configure(
        debug=cmd.verbosity in {PrintLevel.DEBUG, PrintLevel.TRACE},
        trace=cmd.verbosity == PrintLevel.TRACE,
    )

    if settings.h265 and settings.vp9:
        pp.error("Cannot convert to both H265 and VP9")
        raise cappa.Exit(code=1)

    for video in coerce_video_files(clean_cmd.files):
        pp.info(f"⇨ {video.path.name}")
        video.reorder_streams()

        video.process_streams()

    if settings.video_1080:
        video.video_to_1080p()

    if settings.h265:
        video.convert_to_h265()

    if settings.vp9:
        video.convert_to_vp9()

    if not settings.dryrun:
        out_file = tmp_to_output(
            video.temp_file.latest_temp_path(),
            stem=video.stem,
            new_file=settings.out_path,
            overwrite=settings.overwrite,
        )
        video.temp_file.clean_up()

        if settings.overwrite and out_file != video.path:
            pp.debug(f"Delete: {video.path}")
            video.path.unlink()

        pp.success(f"{out_file}")

    raise cappa.Exit(code=0)
