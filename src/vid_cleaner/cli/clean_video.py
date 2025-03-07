"""Clean subcommand."""

import cappa

from vid_cleaner.constants import PrintLevel
from vid_cleaner.utils import coerce_video_files, pp, settings, tmp_to_output
from vid_cleaner.vidcleaner import CleanCommand, VidCleaner


def main(cmd: VidCleaner, clean_cmd: CleanCommand) -> None:
    """Process video files to extract clips based on start time and duration.

    Args:
        cmd (VidCleaner): The main command object containing global options and configuration.
        clean_cmd (CleanCommand): The clean subcommand object containing clean-specific options.

    Raises:
        cappa.Exit: If start or duration times are not in HH:MM:SS format.
    """
    settings.update(
        {
            "dryrun": cmd.dry_run,
            "langs_to_keep": clean_cmd.langs.split(",")
            if clean_cmd.langs
            else settings.keep_languages,
            "subs_drop_local": clean_cmd.subs_drop_local,
            "keep_local_subtitles": clean_cmd.keep_local_subtitles,
            "keep_commentary": clean_cmd.keep_commentary,
            "keep_all_subtitles": clean_cmd.keep_all_subtitles,
            "drop_original_audio": clean_cmd.drop_original_audio,
            "downmix_stereo": clean_cmd.downmix_stereo,
            "overwrite": clean_cmd.overwrite,
            "out_path": clean_cmd.out,
            "h265": clean_cmd.h265,
            "vp9": clean_cmd.vp9,
            "video_1080": clean_cmd.video_1080,
            "force": clean_cmd.force,
        }
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
            video.current_tmp_file,
            stem=video.stem,
            new_file=settings.out_path,
            overwrite=settings.overwrite,
        )
        video.cleanup()

        if settings.overwrite and out_file != video.path:
            pp.debug(f"Delete: {video.path}")
            video.path.unlink()

        pp.success(f"{out_file}")

    raise cappa.Exit(code=0)
