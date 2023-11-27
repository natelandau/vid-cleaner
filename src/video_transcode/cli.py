"""video-transcode CLI."""

import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.table import Table

from video_transcode.__version__ import __version__
from video_transcode.models import VideoFile
from video_transcode.utils import (
    console,
    existing_file_path,
    ffprobe,
    instantiate_logger,
    tmp_to_output,
)

typer.rich_utils.STYLE_HELPTEXT = ""

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode="rich")


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
) -> None:
    """Inspect video files to display detailed stream information.

    Use this command to view detailed information about the video and audio streams
    of a video file. The information includes stream type, codec, language,
    and audio channel details. This command is useful for understanding the
    composition of a video file before performing operations like clipping or transcoding.
    """
    for video in files:
        probe = ffprobe(video.path)

        table = Table(title=probe["format"]["tags"]["title"])
        table.add_column("#")
        table.add_column("Stream")
        table.add_column("Type")
        table.add_column("Language")
        table.add_column("Channels")
        table.add_column("Channel Layout")

        for i, stream in enumerate(probe["streams"]):
            language = stream["tags"].get("language", "-")
            channels = stream.get("channels", "-")
            layout = stream.get("channel_layout", "-")

            table.add_row(
                str(i), stream["codec_type"], stream["codec_name"], language, str(channels), layout
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
    out: Annotated[Optional[Path], typer.Option(help="Output file", show_default=False)] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite output file if it exists")
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
        msg = "Start must be in format HH:MM:SS"
        raise typer.BadParameter(msg)

    if not time_pattern.match(duration):
        msg = "Duration must be in format HH:MM:SS"
        raise typer.BadParameter(msg)

    for video in files:
        logger.info(f"Clipping {video.path.name}")

        video.clip(start, duration)
        out_file = tmp_to_output(
            video.current_tmp_file, stem=video.stem, new_file=out, overwrite=overwrite
        )
        video.cleanup()
        logger.success(f"✅ Clipped video saved to {out_file}")


@app.command("transcode")
def transcode_command(
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
    out: Annotated[Optional[Path], typer.Option(help="Output file", show_default=False)] = None,
    downmix_stereo: Annotated[
        bool,
        typer.Option(
            "--downmix", help="Create a stereo track if none exist", rich_help_panel="Audio"
        ),
    ] = False,
    drop_original_audio: Annotated[
        bool,
        typer.Option("--drop_original", help="Drop original language", rich_help_panel="Audio"),
    ] = False,
    keep_all_subtitles: Annotated[
        bool, typer.Option("--keep_subs", help="Keep all subtitles", rich_help_panel="Subtitles")
    ] = False,
    keep_commentary: Annotated[
        bool, typer.Option("--keep_commentary", help="Keep commentary", rich_help_panel="Audio")
    ] = False,
    keep_local_subtitles: Annotated[
        bool,
        typer.Option(
            "--keep-local-subs", help="Always keep local subtitles", rich_help_panel="Subtitles"
        ),
    ] = False,
    keep_subtitles_if_not_original: Annotated[
        bool,
        typer.Option(
            "--local-when-needed/--no-local",
            help="Keep subtitles if original audio not in langs_to_keep",
            rich_help_panel="Subtitles",
        ),
    ] = True,
    langs: Annotated[
        str,
        typer.Option(
            help="Languages to keep. Comma separated language codes", rich_help_panel="Audio"
        ),
    ] = "eng",
    h265: Annotated[
        bool, typer.Option("--h265", help="Convert to H265", rich_help_panel="Video")
    ] = False,
    vp9: Annotated[
        bool, typer.Option("--vp9", help="Convert to VP9", rich_help_panel="Video")
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite output file if it exists")
    ] = False,
) -> None:
    """Transcode video files to different formats or configurations.

    This command is versatile and allows for a range of transcoding options for video files with various options. You can select various audio and video settings, manage subtitles, and choose the output file format.

    The defaults for this command will:

    * Drop commentary audio tracks
    * Drop all audio languages other than English, unless the original audio is not in English, in which case the original audio is retained
    * Drop all subtitles other than English, unless the original audio is not in English, in which case English subtitles are retained

    The defaults can be overridden by using the various options available.

    Additionally, this script can transcode video files to the H265 or VP9 codecs and downmix audio.  See the options below for more details.

    [bold underline]Usage Examples[/bold underline]

    [#999999]Transcode a video to H265 format and keep English audio:[/#999999]
    transcode --h265 --langs=eng <video_file>

    [#999999]Downmix audio to stereo and keep all subtitles:[/#999999]
    transcode --downmix --keep_subs <video_file>
    """
    for video in files:
        logger.info(f"Transcoding {video.path.name}")

        if h265 and vp9:
            msg = "Cannot convert to both H265 and VP9"
            raise typer.BadParameter(msg)

        video.reorder_streams()
        video.process(
            langs_to_keep=langs.split(","),
            drop_original_audio=drop_original_audio,
            keep_commentary=keep_commentary,
            downmix_stereo=downmix_stereo,
            keep_all_subtitles=keep_all_subtitles,
            keep_local_subtitles=keep_local_subtitles,
            keep_subtitles_if_not_original=keep_subtitles_if_not_original,
        )

        if h265:
            video._convert_to_h265()
            video.reorder_streams()

        if vp9:
            video._convert_to_vp9()

        out_file = tmp_to_output(
            video.current_tmp_file, stem=video.stem, new_file=out, overwrite=overwrite
        )
        video.cleanup()
        logger.success(f"✅ Video saved to {out_file}")


@app.callback()
def main(
    log_file: Path = typer.Option(
        Path(Path.home() / "logs" / f"{__package__}.log"),
        help="Path to log file",
        show_default=True,
        dir_okay=False,
        file_okay=True,
        exists=False,
    ),
    log_to_file: bool = typer.Option(
        False,
        "--log-to-file",
        help="Log to file",
        show_default=True,
    ),
    verbosity: int = typer.Option(
        0,
        "-v",
        "--verbose",
        show_default=True,
        help="""Set verbosity level(0=INFO, 1=DEBUG, 2=TRACE)""",
        count=True,
    ),
    version: Optional[bool] = typer.Option(  # noqa: ARG001
        None, "--version", help="Print version and exit", callback=version_callback, is_eager=True
    ),
) -> None:
    """Inspect and transcode video files."""
    # Instantiate Logging
    instantiate_logger(verbosity, log_file, log_to_file)


if __name__ == "__main__":
    app()
