"""Vidcleaner cli."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Annotated

import cappa
from nclutils import pp
from rich.traceback import install

from vid_cleaner.constants import USER_CONFIG_PATH, PrintLevel
from vid_cleaner.utils import create_default_config, settings, validate_settings


@cappa.command(
    name="vidcleaner",
    description=f"""Transcode video files to different formats or configurations using ffmpeg. This script provides a simple CLI for common video transcoding tasks.

- **Inspect** video files to display detailed stream information
- **Clip** a section from a video file
- **Drop audio streams** containing undesired languages or commentary
- **Drop subtitles** containing undesired languages
- **Keep subtitles** if original audio is not in desired language
- **Downmix audio** to stereo
- **Convert** video files to H265 or VP9

The defaults can be overridden by using the various command line options or by editing the configuration file located at `{USER_CONFIG_PATH}`.

**Usage Examples:**
```shell
# Inspect video file:
vidcleaner inspect <video_file>

# Clip a one minute clip from a video file:
vidcleaner clip --start=00:00:00 --duration=00:01:00 <video_file>

#Transcode a video to H265 format and keep English audio
vidcleaner clean --h265 --langs=eng <video_file>

# Downmix audio to stereo and keep all subtitles
vidcleaner clean --downmix --keep-subs <video_file>
```
    """,
)
class VidCleaner:
    """Transcode video files to different formats or configurations using ffmpeg. This script provides a simple CLI for common video transcoding tasks."""

    command: cappa.Subcommands[CacheCommand | CleanCommand | InspectCommand | ClipCommand]

    verbosity: Annotated[
        PrintLevel,
        cappa.Arg(
            short=True,
            count=True,
            help="Verbosity level (`-v` or `-vv`)",
            choices=[],
            show_default=False,
            propagate=True,
        ),
    ] = PrintLevel.INFO

    dry_run: Annotated[
        bool,
        cappa.Arg(
            long=True,
            short="-n",
            help="Preview changes without modifying files",
            show_default=False,
            propagate=True,
        ),
    ] = False


@cappa.command(
    name="clean",
    invoke="vid_cleaner.cli.clean_video.main",
    help="Clean a video file",
    description=f"""\
Transcode video files to different formats or configurations.

Vidcleaner is versatile and allows for a range of transcoding options for video files with various options. You can select various audio and video settings, manage subtitles, and choose the output file format.

The defaults for this command will:

* Use English as the default language
* Drop commentary audio tracks
* Keep default language audio
* Keep original audio if it is not the default language
* Drop all subtitles unless the original audio is not your local language

Defaults for vid-cleaner are set in the configuration file located at `{USER_CONFIG_PATH}`. When vid-cleaner is run, it will create this file if it does not exist. All options can be overridden on the command line.

**NOTE:** If you've updated your user config file, the flags for the cli will work in reverse order. For example, if you've set `downmix_stereo = true` in your user config file, the flag `--downmix` will actually disable downmixing.

**Important:** Vid-cleaner makes decisions about which audio and subtitle tracks to keep based on the original language of the video. This is determined by querying the TMDb, Radarr, andSonarr APIs. To use this functionality, you must add the appropriate API keys to the configuration file.

**Usage Examples:**
```shell
# Transcode a video to H265 format and keep English audio:
vidcleaner clean --h265 --langs=eng <video_file>

# Downmix audio to stereo and keep all subtitles:
vidcleaner clean --downmix --keep-subs <video_file>
```
    """,
)
class CleanCommand:
    """Clean a video file."""

    files: Annotated[
        list[Path],
        cappa.Arg(help="Video file path(s)"),
    ]
    out: Annotated[
        Path | None,
        cappa.Arg(
            help="Output path (Default: `./<input_file>_1.xxx`)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = None
    overwrite: Annotated[
        bool,
        cappa.Arg(
            help="Do not create a backup of the original file if it would be overwritten",
            long=True,
            show_default=True,
        ),
    ] = False
    downmix_stereo: Annotated[
        bool,
        cappa.Arg(
            help="Create a stereo track if none exist",
            long="--downmix",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.downmix_stereo
    drop_original_audio: Annotated[
        bool,
        cappa.Arg(
            help="Drop original language audio if not specified in langs_to_keep",
            long="--drop-original",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.drop_original_audio
    keep_all_subtitles: Annotated[
        bool,
        cappa.Arg(
            help="Keep all subtitles",
            long="--keep-subs",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.keep_all_subtitles
    save_each_step: Annotated[
        bool,
        cappa.Arg(
            help="By default, the new video is saved after all steps are completed. Use this option to save the video after each step.",
            long="--save-each",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.save_each_step
    keep_commentary: Annotated[
        bool,
        cappa.Arg(
            help="Keep commentary audio",
            long="--keep-commentary",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.keep_commentary
    keep_local_subtitles: Annotated[
        bool,
        cappa.Arg(
            help="Keep subtitles matching the local language(s)",
            long="--keep-local-subs",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.keep_local_subtitles
    drop_local_subs: Annotated[
        bool,
        cappa.Arg(
            help="Force dropping local subtitles even if audio is not default language",
            long="--drop-local-subs",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.drop_local_subs
    langs_to_keep: Annotated[
        str,
        cappa.Arg(
            help="Languages to keep. Comma separated language ISO 639-1 codes (e.g. `en,es`)",
            long="--langs",
            show_default=True,
            group="Configuration",
        ),
    ] = settings.langs_to_keep
    h265: Annotated[
        bool,
        cappa.Arg(
            help="Convert to H265", long="--h265", show_default=True, group="Video Conversion"
        ),
    ] = False
    vp9: Annotated[
        bool,
        cappa.Arg(help="Convert to VP9", long="--vp9", show_default=True, group="Video Conversion"),
    ] = False
    video_1080: Annotated[
        bool,
        cappa.Arg(
            help="Convert to 1080p", long="--1080p", show_default=True, group="Video Conversion"
        ),
    ] = False
    force: Annotated[
        bool,
        cappa.Arg(
            help="Force processing of file even if it is already in the desired format",
            long="--force",
            show_default=True,
        ),
    ] = False


@cappa.command(
    name="inspect",
    invoke="vid_cleaner.cli.inspect_video.main",
    help="Inspect a video file",
    description="""\
Inspect video files to display detailed stream information.

Use this command to view detailed information about the video and audio streams of a video file. The information includes stream type, codec, language, and audio channel details. This command is useful for understanding the composition of a video file before performing operations like clipping or transcoding.
""",
)
class InspectCommand:
    """Inspect a video file."""

    files: Annotated[
        list[Path],
        cappa.Arg(help="Video file path(s)"),
    ]
    json_output: Annotated[
        bool,
        cappa.Arg(
            help="Output in JSON format",
            long="--json",
            short=True,
            show_default=True,
        ),
    ] = False


@cappa.command(
    name="clip",
    invoke="vid_cleaner.cli.clip_video.main",
    help="Clip a video file",
    description="""\
Clip a section from a video file.

This command allows you to extract a specific portion of a video file based on start time and duration.

* The start time and duration should be specified in `HH:MM:SS` format.
* You can also specify an output file to save the clipped video. If the output file is not specified, the clip will be saved with a default naming convention.

Use the `--overwrite` option to avoid creating a backup of the original file if it would be overwritten.
""",
)
class ClipCommand:
    """Clip a video file."""

    files: Annotated[
        list[Path],
        cappa.Arg(help="Video file path(s)"),
    ]

    start: Annotated[
        str,
        cappa.Arg(
            help="Start time in `HH:MM:SS` format (Default: `00:00:00`)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = "00:00:00"
    duration: Annotated[
        str,
        cappa.Arg(
            help="Duration in `HH:MM:SS` format (Default: `00:01:00`)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = "00:01:00"
    out: Annotated[
        Path | None,
        cappa.Arg(
            help="Output file path (Default: `<input_file>_1`)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = None
    overwrite: Annotated[
        bool,
        cappa.Arg(
            help="Do not create a backup of the original file if it would be overwritten",
            long=True,
            show_default=True,
        ),
    ] = False


@cappa.command(
    name="cache",
    help="View and clear the vidcleaner cache",
    invoke="vid_cleaner.cli.cache.main",
)
class CacheCommand:
    """Manage the vidcleaner cache."""

    clear: Annotated[
        bool,
        cappa.Arg(help="Clear the vidcleaner cache", long=True, short=True, show_default=True),
    ] = False


def main() -> None:  # pragma: no cover
    """Main function."""  # noqa: DOC501
    install(show_locals=True)

    try:
        cappa.invoke(obj=VidCleaner, deps=[create_default_config, validate_settings])
    except KeyboardInterrupt as e:
        pp.info("Exiting...")
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
