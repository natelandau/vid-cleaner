"""Vidcleaner cli."""

from __future__ import annotations

import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cappa
from cappa.base import command, invoke
from nclutils import pp
from rich.traceback import install

from vid_cleaner.config import SETTING_NAMES, Config, ConfigError, RunOptions, load_config
from vid_cleaner.constants import USER_CONFIG_PATH, PrintLevel, SortOrder, VerifyLevel, VideoTrait
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import (
    create_default_config,
    find_missing_binaries,
    parse_limit,
    parse_trait_filters,
)

if TYPE_CHECKING:
    from types import FrameType

# What kill, `docker stop`, and systemd send to stop a process, and what a closed terminal sends
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
    """Turn a stop signal into the KeyboardInterrupt that Ctrl-C raises.

    Raises:
        KeyboardInterrupt: Always.
    """
    raise KeyboardInterrupt


def handle_stop_signals() -> None:
    """Stop on SIGTERM and SIGHUP the same way as on Ctrl-C, so temporary files are removed.

    By default these signals end the process at once, skipping exit hooks and the
    interrupt handling that deletes full-size temporary files and staged output copies.
    A signal the parent set to be ignored, such as SIGHUP under `nohup`, stays ignored.
    """
    for stop_signal in STOP_SIGNALS:
        if signal.getsignal(stop_signal) is signal.SIG_IGN:
            continue
        signal.signal(stop_signal, _raise_keyboard_interrupt)


def verify_required_binaries(vidcleaner: VidCleaner) -> None:
    """Stop the run when the external programs a command needs are not installed.

    Fail here rather than at the first file, so a missing program cannot be mistaken for
    a library full of unreadable video.

    Args:
        vidcleaner (VidCleaner): The main CLI application object, used to identify the
            subcommand about to run.

    Raises:
        cappa.Exit: If any required program is absent from PATH.
    """
    # `cache` reads and clears stored API responses, so it never shells out to either.
    if isinstance(vidcleaner.command, CacheCommand):
        return

    if missing := find_missing_binaries():
        pp.error(
            f"Not found on PATH: {', '.join(missing)}",
            details=["vid-cleaner needs ffmpeg and ffprobe. Install ffmpeg to get both."],
        )
        raise cappa.Exit(code=1)


_PRINT_LEVEL_VERBOSITY: dict[PrintLevel, int] = {PrintLevel.DEBUG: 1, PrintLevel.TRACE: 2}


def _output_verbosity(vidcleaner: VidCleaner) -> int:
    return _PRINT_LEVEL_VERBOSITY.get(vidcleaner.verbosity, 0)


def configure_output(vidcleaner: VidCleaner) -> None:
    """Set the output verbosity from the `-v` flags before any command prints.

    Args:
        vidcleaner (VidCleaner): The parsed CLI arguments.
    """
    pp.configure(verbosity=_output_verbosity(vidcleaner))


def build_config(vidcleaner: VidCleaner) -> Config:
    """Load the settings for this run, with the CLI flags that map to settings applied last.

    Only commands that read settings ask for this, so a broken config file cannot stop
    `check`, `search`, `inspect`, or `--help`.

    Args:
        vidcleaner (VidCleaner): The parsed CLI arguments.

    Returns:
        Config: The validated settings.

    Raises:
        cappa.Exit: If any setting is invalid.
    """
    command = vidcleaner.command
    cli_overrides = {
        key: value for key in SETTING_NAMES if (value := getattr(command, key, None)) is not None
    }

    try:
        loaded = load_config(cli_overrides=cli_overrides)
    except ConfigError as e:
        for error in e.errors:
            pp.error(error)
        raise cappa.Exit(code=1) from e

    for warning in loaded.warnings:
        pp.warning(warning)

    pp.trace("Settings", details=[loaded.config.redacted()])
    return loaded.config


def build_run_options(vidcleaner: VidCleaner) -> RunOptions:
    """Collect the CLI-only values for this run that are not settings.

    Args:
        vidcleaner (VidCleaner): The parsed CLI arguments.

    Returns:
        RunOptions: The values from the command line, with defaults for flags the command lacks.
    """
    command = vidcleaner.command
    return RunOptions(
        dryrun=vidcleaner.dryrun,
        verbosity=_output_verbosity(vidcleaner),
        h265=getattr(command, "h265", False),
        vp9=getattr(command, "vp9", False),
        video_1080=getattr(command, "video_1080", False),
        force=getattr(command, "force", False),
        out=getattr(command, "out", None),
    )


@dataclass
class SelectionOptions:
    """The query vocabulary that names a set of files without narrowing it to a count.

    Split from `DiscoveryOptions` so `check` can share the query language without
    inheriting `--limit`: truncating a validator's selection would let it exit 0 while
    leaving files unchecked.
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
        set[VideoTrait] | None,
        cappa.Arg(
            help=f"Comma separated list of facets a file must have all of. Valid options: {VideoTrait.help_options()}",
            long=True,
            show_default=False,
            group="Discovery",
            parse=parse_trait_filters,
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


@dataclass
class DiscoveryOptions(SelectionOptions):
    """The query vocabulary shared by `search` and `clean`.

    Composed into both commands so a `search` invocation and a `clean` invocation with
    the same flags always select the same files.
    """

    limit: Annotated[
        int | None,
        cappa.Arg(
            help="Act on only the first N results after sorting",
            long=True,
            show_default=False,
            group="Discovery",
            parse=parse_limit,
        ),
    ] = None


@command(
    name="vidcleaner",
    description=f"""Transcode video files to different formats or configurations using ffmpeg. This script provides a simple CLI for common video transcoding tasks.

- **Inspect** video files to display detailed stream information
- **Check** that video files are valid
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
        CacheCommand | CheckCommand | CleanCommand | InspectCommand | ClipCommand | SearchCommand
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


@command(
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

Each on-or-off option in `Configuration` also has a `--no-` form, such as `--no-downmix`. A flag overrides the matching `VIDCLEANER_*` environment variable and the configuration file.

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
            help="Output path (Default: replace the input file, keeping a timestamped `.bak` copy of the original)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = None
    overwrite: Annotated[
        bool | None,
        cappa.Arg(
            help="Do not create a backup of the original file if it would be overwritten",
            long="--overwrite",
            short="-w",
            action=cappa.ArgAction.store_true,
        ),
        cappa.Arg(long="--no-overwrite", action=cappa.ArgAction.store_false),
    ] = None
    verify: Annotated[
        VerifyLevel | None,
        cappa.Arg(
            help="Check the output before it replaces the original (Default: quick)",
            long="--verify",
            show_default=False,
            group="Configuration",
        ),
    ] = None
    downmix_stereo: Annotated[
        bool | None,
        cappa.Arg(
            help="Create a stereo track if none exists (add --force to recreate an existing one)",
            long="--downmix",
            short="-d",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(long="--no-downmix", action=cappa.ArgAction.store_false, group="Configuration"),
    ] = None
    drop_original_audio: Annotated[
        bool | None,
        cappa.Arg(
            help="Drop original language audio if not specified in langs_to_keep",
            long="--drop-original",
            short="-D",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(
            long="--no-drop-original", action=cappa.ArgAction.store_false, group="Configuration"
        ),
    ] = None
    keep_all_subtitles: Annotated[
        bool | None,
        cappa.Arg(
            help="Keep all subtitles",
            long="--keep-subs",
            short="-s",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(long="--no-keep-subs", action=cappa.ArgAction.store_false, group="Configuration"),
    ] = None
    keep_commentary: Annotated[
        bool | None,
        cappa.Arg(
            help="Keep commentary audio",
            long="--keep-commentary",
            short="-c",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(
            long="--no-keep-commentary", action=cappa.ArgAction.store_false, group="Configuration"
        ),
    ] = None
    keep_local_subtitles: Annotated[
        bool | None,
        cappa.Arg(
            help="Keep subtitles matching the local language(s)",
            long="--keep-local-subs",
            short="-S",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(
            long="--no-keep-local-subs", action=cappa.ArgAction.store_false, group="Configuration"
        ),
    ] = None
    drop_local_subs: Annotated[
        bool | None,
        cappa.Arg(
            help="Force dropping local subtitles even if audio is not default language",
            long="--drop-local-subs",
            short="-x",
            action=cappa.ArgAction.store_true,
            group="Configuration",
        ),
        cappa.Arg(
            long="--no-drop-local-subs", action=cappa.ArgAction.store_false, group="Configuration"
        ),
    ] = None
    langs_to_keep: Annotated[
        str | None,
        cappa.Arg(
            help="Languages to keep. Comma separated language ISO 639-1 codes (e.g. `en,es`)",
            long="--langs",
            short="-l",
            show_default=False,
            group="Configuration",
        ),
    ] = None
    h265: Annotated[
        bool,
        cappa.Arg(
            help="Convert to H265",
            long="--h265",
            short="-H",
            show_default=True,
            group="Video Conversion",
        ),
    ] = False
    vp9: Annotated[
        bool,
        cappa.Arg(
            help="Convert to VP9",
            long="--vp9",
            short="-V",
            show_default=True,
            group="Video Conversion",
        ),
    ] = False
    video_1080: Annotated[
        bool,
        cappa.Arg(
            help="Convert to 1080p",
            long="--1080p",
            short="-p",
            show_default=True,
            group="Video Conversion",
        ),
    ] = False
    force: Annotated[
        bool,
        cappa.Arg(
            help="Force processing of file even if it is already in the desired format. With --downmix, recreate the stereo track even if one already exists",
            long="--force",
            short="-f",
            show_default=True,
        ),
    ] = False
    yes: Annotated[
        bool,
        cappa.Arg(
            help="Skip the confirmation prompt. Requires `--from`",
            long=True,
            short="-y",
            show_default=True,
        ),
    ] = False
    discovery: cappa.Destructured[DiscoveryOptions] = field(default_factory=DiscoveryOptions)


@command(
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


@command(
    name="clip",
    invoke="vid_cleaner.cli.clip_video.main",
    help="Clip a video file",
    description="""\
Clip a section from a video file.

This command allows you to extract a specific portion of a video file based on start time and duration.

* The start time and duration should be specified in `HH:MM:SS` format.
* Use `--out` to write the clip to a new file. Without it the clip replaces the input file, and the original is kept alongside it as a timestamped `.bak` copy.

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
            help="Output file path (Default: replace the input file, keeping a timestamped `.bak` copy of the original)",
            long=True,
            short=True,
            show_default=False,
        ),
    ] = None
    overwrite: Annotated[
        bool | None,
        cappa.Arg(
            help="Do not create a backup of the original file if it would be overwritten",
            long="--overwrite",
            action=cappa.ArgAction.store_true,
        ),
        cappa.Arg(long="--no-overwrite", action=cappa.ArgAction.store_false),
    ] = None
    verify: Annotated[
        VerifyLevel | None,
        cappa.Arg(
            help="Check the output before it replaces the original (Default: quick)",
            long="--verify",
            show_default=False,
        ),
    ] = None


@command(
    name="check",
    invoke="vid_cleaner.cli.check_video.main",
    help="Check that video files are valid",
    description="""\
Report whether each video file is valid, and exit non-zero when any file is not.

Use this to find damaged and mislabeled files in a library. The command prints one line for every file, so one invalid file does not hide the state of the others. Valid files go to stdout and invalid files to stderr.

A file is valid when all of these conditions are true:

* The file exists
* The file has a video container extension
* ffprobe can read the file
* The file has a minimum of one video stream

Cover art does not count as a video stream. An audio-only file with an embedded poster is invalid.

By default the command reads only the metadata of each file, which takes milliseconds. To decode every frame instead, use `--deep`. A deep check finds damage in the body of a file whose header is intact. It costs minutes for each file.

Name the files directly, or use `--from` to check every video file under a directory. `check` accepts the same query flags as `search`, but it refuses `--limit`, because a truncated selection can exit `0` while files stay unchecked. A trait filter never hides an invalid file, because a file that ffprobe cannot read has no traits.

**Exit codes:** `0` when every file is valid, `1` when any file is not.

**Usage Examples:**
```shell
# Two files named directly
vidcleaner check movie.mkv other.mp4

# Every .mkv file in the current directory
vidcleaner check *.mkv

# A whole library, three levels deep
vidcleaner check --from /media --depth=3

# Fully decode every 4k file to find damage that ffprobe cannot see
vidcleaner check --from /media --filters=4k --deep
```
""",
)
class CheckCommand:
    """Check that video files are valid."""

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
    deep: Annotated[
        bool,
        cappa.Arg(
            help="Decode every frame to find damage ffprobe cannot see (minutes per file)",
            long=True,
            show_default=True,
        ),
    ] = False
    discovery: cappa.Destructured[SelectionOptions] = field(default_factory=SelectionOptions)


@command(
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


@command(
    name="search",
    help="Search for video files under a directory",
    description="""\
This command allows you to search for video files under a directory and display detailed information about the video and audio streams of a video file. The information includes stream type, codec, language, and audio channel details. This command is useful for understanding the composition of a video file before performing operations like clipping or transcoding.

By using filters, you can search for video files that match specific criteria. Every filter you name must match, so listing more of them narrows the results. For example, `--filters=h264,1080p` returns only files that are in the H264 codec *and* have a resolution of 1080p.

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
    ] = Path()
    discovery: cappa.Destructured[DiscoveryOptions] = field(default_factory=DiscoveryOptions)


def main() -> None:  # pragma: no cover
    """Main function."""
    install(show_locals=False)
    handle_stop_signals()

    try:
        invoke(
            obj=VidCleaner,
            deps=[create_default_config, verify_required_binaries, configure_output],
            completion=False,
        )
    except KeyboardInterrupt as e:
        pp.info("\nExiting...")
        raise cappa.Exit(code=1) from e
    except VideoProbeError as e:
        # Only `inspect` and `clip` reach this: `search` skips unreadable files itself, and
        # `clean` catches this per file in its batch loop rather than letting it propagate.
        pp.error(str(e))
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
