"""Vidcleaner cli."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import cappa
from nclutils import pp
from rich.traceback import install

from vid_cleaner import settings
from vid_cleaner.config import SettingsManager
from vid_cleaner.constants import USER_CONFIG_PATH, PrintLevel, SortOrder, VideoTrait
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import create_default_config, parse_trait_filters


def config_subcommand(vidcleaner: VidCleaner) -> None:
    """Configure settings based on the provided command and arguments.

    Update the global settings object with values from the command line arguments and configuration file. Handle project-specific settings if a project is specified.

    Args:
        vidcleaner (VidCleaner): The main CLI application object containing command and configuration options.
    """
    nllog_level = (
        1
        if vidcleaner.verbosity == PrintLevel.DEBUG
        else 2
        if vidcleaner.verbosity == PrintLevel.TRACE
        else 0
    )
    pp.configure(verbosity=nllog_level)

    langs_to_keep = getattr(vidcleaner.command, "langs_to_keep", None)
    if langs_to_keep and isinstance(langs_to_keep, str):
        langs_to_keep = langs_to_keep.split(",")

    # Discovery options are destructured onto a nested object shared by `search` and
    # `clean`, so the raw filter string is one level down from the command itself.
    discovery = getattr(vidcleaner.command, "discovery", None)
    raw_filters = getattr(discovery, "filters", None)
    filters = parse_trait_filters(raw_filters) if raw_filters else set()

    # Apply command-specific settings
    cli_settings = {
        "downmix_stereo": getattr(vidcleaner.command, "downmix_stereo", False),
        "drop_local_subs": getattr(vidcleaner.command, "drop_local_subs", False),
        "drop_original_audio": getattr(vidcleaner.command, "drop_original_audio", False),
        "dryrun": getattr(vidcleaner, "dryrun", False),
        "filters": filters,
        "force": getattr(vidcleaner.command, "force", False),
        "h265": getattr(vidcleaner.command, "h265", False),
        "keep_all_subtitles": getattr(vidcleaner.command, "keep_all_subtitles", False),
        "keep_commentary": getattr(vidcleaner.command, "keep_commentary", False),
        "keep_local_subtitles": getattr(vidcleaner.command, "keep_local_subtitles", False),
        "langs_to_keep": langs_to_keep,
        "out_path": getattr(vidcleaner.command, "out", None),
        "overwrite": getattr(vidcleaner.command, "overwrite", False),
        "subcommand": vidcleaner.command.__class__.__name__.lower(),
        "verbosity": nllog_level,
        "video_1080": getattr(vidcleaner.command, "video_1080", False),
        "vp9": getattr(vidcleaner.command, "vp9", False),
    }

    SettingsManager.apply_cli_settings(cli_settings)

    pp.trace("Settings", details=[settings.to_dict(), vidcleaner.__dict__])


@dataclass
class DiscoveryOptions:
    """The query vocabulary shared by `search` and `clean`.

    Composed into both commands so a `search` invocation and a `clean` invocation with
    the same flags always select the same files.
    """

    depth: Annotated[
        int,
        cappa.Arg(
            help="Depth to search for video files",
            long=True,
            show_default=False,
            group="Discovery",
        ),
    ] = 0
    filters: Annotated[
        str | None,
        cappa.Arg(
            help=f"Comma separated list of facets to search for. Valid options: {VideoTrait.help_options()}",
            long=True,
            show_default=False,
            group="Discovery",
        ),
    ] = None
    sort: Annotated[
        SortOrder,
        cappa.Arg(
            help="Sort results by name, file size, or bitrate.",
            long=True,
            show_default=True,
            group="Discovery",
        ),
    ] = SortOrder.ALPHA
    reverse: Annotated[
        bool,
        cappa.Arg(
            help="Reverse the sort order",
            long=True,
            show_default=True,
            group="Discovery",
        ),
    ] = False
    limit: Annotated[
        int | None,
        cappa.Arg(
            help="Act on only the first N results after sorting",
            long=True,
            show_default=False,
            group="Discovery",
        ),
    ] = None


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

    command: cappa.Subcommands[
        CacheCommand | CleanCommand | InspectCommand | ClipCommand | SearchCommand
    ]

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

    dryrun: Annotated[
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

# Convert the five largest 4k files under a directory to H265:
vidcleaner clean --from /media --filters=4k --sort=size --limit=5 --h265
```
    """,
)
class CleanCommand:
    """Clean a video file."""

    files: Annotated[
        list[Path],
        cappa.Arg(help="Video file path(s)", show_default=False),
    ] = field(default_factory=list)
    from_: Annotated[
        Path | None,
        cappa.Arg(
            help="Directory to discover files in instead of naming them explicitly",
            long="--from",
            show_default=False,
            group="Discovery",
        ),
    ] = None
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
            help="Create a stereo track if none exists (add --force to recreate an existing one)",
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
        # Join to a string default so the dataclass field is hashable; dynaconf >=3.3 returns an
        # unhashable DataList that cappa's @dataclass rejects as a mutable default.
    ] = ",".join(settings.langs_to_keep)
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
            help="Force processing of file even if it is already in the desired format. With --downmix, recreate the stereo track even if one already exists",
            long="--force",
            show_default=True,
        ),
    ] = False
    yes: Annotated[
        bool,
        cappa.Arg(
            help="Skip the confirmation prompt when using `--from`",
            long=True,
            short="-y",
            show_default=True,
        ),
    ] = False
    discovery: cappa.Destructured[DiscoveryOptions] = field(default_factory=DiscoveryOptions)


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


@cappa.command(
    name="search",
    help="Search for video files under a directory",
    description="""\
This command allows you to search for video files under a directory and display detailed information about the video and audio streams of a video file. The information includes stream type, codec, language, and audio channel details. This command is useful for understanding the composition of a video file before performing operations like clipping or transcoding.

By using filters, you can search for video files that match specific criteria. For example, you can search for video files that are in the H264 codec and have a resolution of 1080p.

Files that can not be read as video, such as corrupt files or non-video files carrying a video extension, are counted and skipped rather than ending the search. Run with `-v` to list them.

Results are sorted alphabetically by path. Use `--sort=size` or `--sort=bitrate` to order by
file size or overall bitrate instead, largest first. `--reverse` flips whichever order is active.

**Usage Examples:**
```shell
# Search for video files that are in the H264 codec and have a resolution of 1080p up to 2 levels deep:
vidcleaner search --filters=h264,1080p --depth=2

# List 4k files, largest first:
vidcleaner search --filters=4k --sort=size

# List the 5 largest 4k files:
vidcleaner search --filters=4k --sort=size --limit=5
```
""",
    invoke="vid_cleaner.cli.search.main",
)
class SearchCommand:
    """Search for video files under a directory."""

    directory: Annotated[
        Path,
        cappa.Arg(help="Directory to search for video files", show_default=False),
    ] = Path.cwd()
    discovery: cappa.Destructured[DiscoveryOptions] = field(default_factory=DiscoveryOptions)


def main() -> None:  # pragma: no cover
    """Main function."""
    install(show_locals=False)

    try:
        cappa.invoke(
            obj=VidCleaner, deps=[create_default_config, config_subcommand], completion=False
        )
    except KeyboardInterrupt as e:
        pp.info("\nExiting...")
        raise cappa.Exit(code=1) from e
    except VideoProbeError as e:
        # Commands given explicit files abort on an unreadable one; `search` handles its own
        # skipping before the error can reach here.
        pp.error(str(e))
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
