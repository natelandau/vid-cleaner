"""Helper utilities."""

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ffmpeg as python_ffmpeg
from box import Box
from ffmpeg_progress_yield import FfmpegProgress
from nclutils.sh import ShellCommandError, run_command

from vid_cleaner.constants import FFMPEG_APPEND, FFMPEG_PREPEND, AudioLayout, CodecTypes
from vid_cleaner.exceptions import VideoProbeError


@dataclass(frozen=True)
class FfmpegCommand:
    """A complete ffmpeg command line and the file it writes.

    Attributes:
        arguments (list[str]): The command line, starting with `ffmpeg`.
        output_path (Path): The file the command writes.
    """

    arguments: list[str]
    output_path: Path


def build_ffmpeg_command(
    arguments: list[str], *, input_path: Path, output_path: Path
) -> FfmpegCommand:
    """Wrap one step's ffmpeg arguments in the full command line every step shares.

    Args:
        arguments (list[str]): The step's arguments, placed between the input and the output.
        input_path (Path): The file ffmpeg reads.
        output_path (Path): The file ffmpeg writes.

    Returns:
        FfmpegCommand: The command and the file it writes.
    """
    # Global options go before the input so they apply to the whole run
    command: list[str] = [
        "ffmpeg",
        *FFMPEG_PREPEND,
        "-i",
        str(input_path),
        *arguments,
        *FFMPEG_APPEND,
        str(output_path),
    ]
    return FfmpegCommand(arguments=command, output_path=output_path)


def run_ffmpeg(command: FfmpegCommand, *, on_progress: Callable[[float], None]) -> None:
    """Run an ffmpeg command to completion, reporting progress as it goes.

    A KeyboardInterrupt propagates so the caller can clean up the partial output.

    Args:
        command (FfmpegCommand): The command to run.
        on_progress (Callable[[float], None]): Receives the percent complete as ffmpeg
            reports it.
    """
    for complete in FfmpegProgress(command.arguments).run_command_with_progress():
        on_progress(complete)


def channels_to_layout(channels: int) -> AudioLayout | None:
    """Convert number of audio channels to an AudioLayout enum value.

    Convert a raw channel count into the appropriate AudioLayout enum value for use in audio processing. Handle special cases where 5 channels maps to SURROUND5 (5.1) and 7 channels maps to SURROUND7 (7.1).

    Args:
        channels (int): Number of audio channels in the stream

    Returns:
        AudioLayout | None: The corresponding AudioLayout enum value if a valid mapping exists,
            None if no valid mapping is found

    Examples:
        >>> channels_to_layout(2)
        <AudioLayout.STEREO: 2>
        >>> channels_to_layout(5)
        <AudioLayout.SURROUND5: 6>
        >>> channels_to_layout(7)
        <AudioLayout.SURROUND7: 8>
        >>> channels_to_layout(3)
    """
    if channels in [layout.value for layout in AudioLayout]:
        return AudioLayout(channels)

    if channels == 5:  # noqa: PLR2004
        return AudioLayout.SURROUND5

    if channels == 7:  # noqa: PLR2004
        return AudioLayout.SURROUND7

    return None


def run_ffprobe(path: Path) -> dict[str, Any]:  # pragma: no cover
    """Probe video file and return a dict.

    Args:
        path (Path): Path to video file

    Returns:
        dict: A dictionary containing information about the video file.

    Raises:
        VideoProbeError: If the file is corrupt or is not actually a video despite its extension.
    """
    try:
        return python_ffmpeg.probe(path)
    except python_ffmpeg.Error as e:
        raise VideoProbeError.from_ffprobe_stderr(path=path, stderr=e.stderr) from e


def decode_check(path: Path) -> str | None:
    """Decode every frame of a file and report the first error ffmpeg raises.

    ffprobe reads only the header, so a file whose index and metadata are intact can
    still be unplayable partway through. Decoding every video and audio stream to the
    null muxer forces the whole bitstream through the decoders without writing output,
    which is the only way to catch that. Expect this to take minutes per file.

    Args:
        path (Path): Path to the video file to decode.

    Returns:
        str | None: The first error line ffmpeg reported, or None when it decoded cleanly.
    """
    # `-map 0:v? -map 0:a?` overrides ffmpeg's default stream selection, which would pick
    # only the "best" video and audio stream and leave damage in the other audio tracks
    # undetected. `?` keeps a file without one of the two kinds from being an error.
    try:
        result = run_command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
            ],
            check=False,
        )
    except ShellCommandError as e:
        return str(e)

    # `-v error` silences the banner and progress, so anything on stderr is a real
    # diagnostic. Trust it over the exit code, which stays 0 for recoverable errors.
    for line in result.stderr_lines:
        if stripped := line.strip():
            return stripped

    return None if result.ok else f"ffmpeg exited with code {result.returncode}"


def get_probe_as_box(input_path: Path) -> Box:
    """Parse ffprobe output into a Box object with normalized stream and format data.

    Convert the raw ffprobe JSON output into a Box object with standardized fields. Extract and normalize metadata like title, format details, duration, and stream properties. Convert codec types and audio channels to enums.

    Args:
        input_path (Path): Path to the video file to probe

    Returns:
        Box: Box object containing normalized probe data with fields for format metadata and stream properties

    Raises:
        VideoProbeError: If the file cannot be probed or its streams cannot be normalized.
    """
    raw_output = run_ffprobe(input_path)
    probe_box = Box(
        raw_output,
        default_box=True,
        default_box_create_on_get=False,
    )
    # Normalization below mutates the streams, so keep the untouched ffprobe output for JSON reports.
    probe_box.ffprobe_output = copy.deepcopy(raw_output)

    probe_box.path_to_file = input_path
    probe_box.name = probe_box.format.tags.title or probe_box.format.filename or input_path.name
    probe_box.format_name = probe_box.format.format_name or None
    probe_box.format_long_name = probe_box.format.format_long_name or None
    probe_box.duration = probe_box.format.duration or None
    probe_box.start_time = probe_box.format.start_time or None
    probe_box.size = probe_box.format.size or None
    probe_box.bit_rate = probe_box.format.bit_rate or None

    # Set stream codecs to enum
    try:
        for stream in probe_box.streams:
            stream.codec_type = CodecTypes(str(stream.codec_type).lower())
            # ffprobe omits `codec_name` for streams it cannot identify; the rest of the codebase
            # calls `.lower()` on it unconditionally.
            stream.codec_name = stream.codec_name if isinstance(stream.codec_name, str) else ""
            stream.bps = stream.tags.BPS or None
            stream.title = stream.tags.title or None
            # Preserve the raw count before it is bucketed into an enum; the downmix logic needs
            # it to detect >7.1 (Atmos) layouts, which have no AudioLayout member.
            stream.channel_count = stream.channels if isinstance(stream.channels, int) else None
            stream.channels = channels_to_layout(stream.channels)
            stream.language = stream.language or stream.tags.language or None
    except (AttributeError, TypeError, ValueError) as e:
        # ffprobe can succeed on a non-video file and still report streams this model cannot
        # describe; treat that as unreadable rather than crashing the caller.
        raise VideoProbeError(path=input_path, reason=str(e)) from e

    return probe_box
