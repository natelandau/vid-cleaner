"""vid-cleaner CLI."""
import re
import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer
from confz import validate_all_configs
from loguru import logger
from pydantic import ValidationError
from rich.table import Table

from vid_cleaner.__version__ import __version__
from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.constants import CONFIG_PATH
from vid_cleaner.models import VideoFile
from vid_cleaner.utils import (
    console,
    existing_file_path,
    ffprobe,
    instantiate_logger,
    tmp_to_output,
)

typer.rich_utils.STYLE_HELPTEXT = ""

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def docstring_parameter(*sub):  # type: ignore [no-untyped-def]
    """Decorator to format docstring with parameters."""

    def dec(obj):  # type: ignore [no-untyped-def]
        """Format docstring with parameters."""
        obj.__doc__ = obj.__doc__.format(*sub)
        return obj

    return dec


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"{__package__}: v{__version__}")
        raise typer.Exit()


def parse_video_input(path: str) -> VideoFile:
    """Takes a string of a path and converts it to a VideoFile object."""
    file_path = existing_file_path(path)
    if file_path.suffix not in {".mkv", ".mp4", ".avi", "vp9", ".webm", ".mov", ".wmv"}:
        msg = f"File type '{file_path.suffix}' is not supported"
        raise typer.BadParameter(msg)

    return VideoFile(file_path)


@app.command("inspect")
def inspect_command(
    files: Annotated[
        list[VideoFile],
        typer.Argument(
            parser=parse_video_input,
            help="Path to video file(s)",
            show_default=False,
            exists=True,
            file_okay=True,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    json: Annotated[bool, typer.Option(help="Output in JSON format")] = False,
) -> None:
    """Inspect video files to display detailed stream information.

    Use this command to view detailed information about the video and audio streams
    of a video file. The information includes stream type, codec, language,
    and audio channel details. This command is useful for understanding the
    composition of a video file before performing operations like clipping or transcoding.
    """
    for video in files:
        probe = ffprobe(video.path)

        if json:
            console.print(probe)
            return

        if "title" in probe["format"]["tags"]:
            name = probe["format"]["tags"]["title"]
        elif "filename" in probe["format"]:
            name = probe["format"]["filename"]
        else:
            name = video.stem

        table = Table(title=name)
        table.add_column("#")
        table.add_column("Stream")
        table.add_column("Type")
        table.add_column("Language")
        table.add_column("Channels")
        table.add_column("Channel Layout")
        table.add_column("Title")

        for i, stream in enumerate(probe["streams"]):
            language = stream["tags"].get("language", "-")
            channels = stream.get("channels", "-")
            layout = stream.get("channel_layout", "-")
            title = stream["tags"].get("title", "-")

            table.add_row(
                str(i),
                stream["codec_type"],
                stream.get("codec_name", "-"),
                language,
                str(channels),
                layout,
                title,
            )

        console.print(table)


@app.command("clip")
def clip_command(
    files: Annotated[
        list[VideoFile],
        typer.Argument(
            parser=parse_video_input,
            help="Path to video file(s)",
            show_default=False,
            exists=True,
            file_okay=True,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    start: Annotated[str, typer.Option(help="Start time 'HH:MM:SS'")] = "00:00:00",
    duration: Annotated[str, typer.Option(help="Duration to clip 'HH:MM:SS'")] = "00:01:00",
    out: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            "-o",
            help=r"Output file [#888888]\[default: input_file_1][/#888888]",
            show_default=False,
        ),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite output file if it exists")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show ffmpeg commands without executing")
    ] = False,
) -> None:
    """Clip a section from a video file.

    This command allows you to extract a specific portion of a video file based on start time and duration.

    * The start time and duration should be specified in [code]HH:MM:SS[/code] format.
    * You can also specify an output file to save the clipped video. If the output file is not specified, the clip will be saved with a default naming convention.

    Use the [code]--overwrite[/code] option to overwrite the output file if it already exists.
    """
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    if not time_pattern.match(start):
        msg = "Start must be in format HH:MM:SS"  # type: ignore [unreachable]
        raise typer.BadParameter(msg)

    if not time_pattern.match(duration):
        msg = "Duration must be in format HH:MM:SS"  # type: ignore [unreachable]
        raise typer.BadParameter(msg)

    for video in files:
        logger.info(f"⇨ {video.path.name}")

        video.clip(start, duration, dry_run=dry_run)

        if not dry_run:
            out_file = tmp_to_output(
                video.current_tmp_file, stem=video.stem, new_file=out, overwrite=overwrite
            )
            video.cleanup()
            logger.success(f"{out_file}")


@docstring_parameter(CONFIG_PATH)
@app.command("clean")
def clean_command(
    files: Annotated[
        list[VideoFile],
        typer.Argument(
            parser=parse_video_input,
            help="Path to video file(s)",
            show_default=False,
            exists=True,
            file_okay=True,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    out: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            "-o",
            help=r"Output file [#888888]\[default: input_file_1][/#888888]",
            show_default=False,
        ),
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            "-r",
            help="Delete or overwrite original file after processing. Use with caution",
        ),
    ] = False,
    downmix_stereo: Annotated[
        bool,
        typer.Option(
            "--downmix", help="Create a stereo track if none exist", rich_help_panel="Audio"
        ),
    ] = False,
    drop_original_audio: Annotated[
        bool,
        typer.Option(
            "--drop-original", help="Drop original language audio", rich_help_panel="Audio"
        ),
    ] = False,
    keep_all_subtitles: Annotated[
        bool, typer.Option("--keep-subs", help="Keep all subtitles", rich_help_panel="Subtitles")
    ] = False,
    keep_commentary: Annotated[
        bool,
        typer.Option("--keep-commentary", help="Keep commentary audio", rich_help_panel="Audio"),
    ] = False,
    keep_local_subtitles: Annotated[
        bool,
        typer.Option(
            "--keep-local-subs",
            help="Keep subtitles matching the local languages but drop all others",
            rich_help_panel="Subtitles",
        ),
    ] = False,
    subs_drop_local: Annotated[
        bool,
        typer.Option(
            "--drop-local-subs",
            help="Force dropping local subtitles even if audio is not default language",
            rich_help_panel="Subtitles",
        ),
    ] = False,
    langs: Annotated[
        Optional[str],
        typer.Option(
            help="Languages to keep. Comma separated language codes",
            rich_help_panel="Audio",
            show_default=False,
        ),
    ] = None,
    h265: Annotated[
        bool, typer.Option("--h265", help="Convert to H265", rich_help_panel="Video")
    ] = False,
    vp9: Annotated[
        bool, typer.Option("--vp9", help="Convert to VP9", rich_help_panel="Video")
    ] = False,
    video_1080: Annotated[
        bool, typer.Option("--1080p", help="Convert to 1080p", rich_help_panel="Video")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Force processing of file even if it is already in the desired format",
            rich_help_panel="Video",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show ffmpeg commands without executing")
    ] = False,
) -> None:
    """Transcode video files to different formats or configurations.

    Vidcleaner is versatile and allows for a range of transcoding options for video files with various options. You can select various audio and video settings, manage subtitles, and choose the output file format.

    The defaults for this command will:

    * Use English as the default language
    * Drop commentary audio tracks
    * Keep default language audio
    * Keep original audio if it is not the default language
    * Drop all subtitles unless the original audio is not in the default language, in which case the default subtitles are retained

    The defaults can be overridden by using the various command line options or by editing the configuration file located at [code]{0}[/code]

    [bold underline]Usage Examples[/bold underline]

    [#999999]Transcode a video to H265 format and keep English audio:[/#999999]
    vidcleaner clean --h265 --langs=eng <video_file>

    [#999999]Downmix audio to stereo and keep all subtitles:[/#999999]
    vidcleaner clean --downmix --keep-subs <video_file>
    """
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


@docstring_parameter(CONFIG_PATH)
@app.callback()
def main(
    log_file: Annotated[
        Optional[Path],
        typer.Option(
            help="Path to log file",
            show_default=False,
            dir_okay=False,
            file_okay=True,
            exists=False,
            rich_help_panel="Output Settings",
        ),
    ] = None,
    log_to_file: Annotated[
        Optional[bool],
        typer.Option(
            "--log-to-file",
            help="Log to file",
            show_default=True,
            rich_help_panel="Output Settings",
        ),
    ] = None,
    verbosity: Annotated[
        int,
        typer.Option(
            "-v",
            "--verbose",
            show_default=True,
            help="""Set verbosity level(0=INFO, 1=DEBUG, 2=TRACE)""",
            count=True,
            rich_help_panel="Output Settings",
        ),
    ] = 0,
    version: Annotated[  # noqa: ARG001
        Optional[bool],
        typer.Option(
            "--version",
            is_eager=True,
            callback=version_callback,
            help="Print version and exit",
            rich_help_panel="Output Settings",
        ),
    ] = None,
) -> None:
    """Transcode video files to different formats or configurations using ffmpeg. This script provides a simple CLI for common video transcoding tasks.

    \b
    - [bold]Inspect[/bold] video files to display detailed stream information
    - [bold]Clip[/bold] a section from a video file
    - [bold]Drop audio streams[/bold] containing undesired languages or commentary
    - [bold]Drop subtitles[/bold] containing undesired languages
    - [bold]Keep subtitles[/bold] if original audio is not in desired language
    - [bold]Downmix audio[/bold] to stereo
    - [bold]Convert[/bold] video files to H265 or VP9

    The defaults can be overridden by using the various command line options or by editing the configuration file located at [code]{0}[/code]

    [bold underline]Usage Examples[/bold underline]

        [#999999]Inspect video file:[/#999999]
        vidcleaner inspect <video_file>

        [#999999]Clip a one minute clip from a video file:[/#999999]
        vidcleaner clip --start=00:00:00 --duration=00:01:00 <video_file>

        [#999999]Transcode a video to H265 format and keep English audio:[/#999999]
        vidcleaner clean --h265 --langs=eng <video_file>

        [#999999]Downmix audio to stereo and keep all subtitles:[/#999999]
        vidcleaner clean --downmix --keep-subs <video_file>
    """  # noqa: D301
    # Instantiate Logging
    instantiate_logger(verbosity, log_file, log_to_file)

    # Create a default configuration file if one does not exist
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        default_config_file = Path(__file__).parent.resolve() / "default_config.toml"
        shutil.copy(default_config_file, CONFIG_PATH)

    # Load and validate configuration
    try:
        validate_all_configs()
    except ValidationError as e:
        logger.error(f"Invalid configuration file: {CONFIG_PATH}")
        for error in e.errors():
            console.print(f"           [red]{error['loc'][0]}: {error['msg']}[/red]")
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
