"""Helper utilities."""

from pathlib import Path

import cappa
import ffmpeg as python_ffmpeg
from box import Box
from nclutils import pp

from vid_cleaner.constants import AudioLayout, CodecTypes


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


def run_ffprobe(path: Path) -> dict:  # pragma: no cover
    """Probe video file and return a dict.

    Args:
        path (Path): Path to video file

    Returns:
        dict: A dictionary containing information about the video file.

    Raises:
        cappa.Exit: If an error occurs while probing the video file.
    """
    try:
        probe = python_ffmpeg.probe(path)
    except python_ffmpeg.Error as e:
        pp.error(e.stderr)
        raise cappa.Exit(code=1) from e

    return probe


def get_probe_as_box(input_path: Path) -> Box:
    """Parse ffprobe output into a Box object with normalized stream and format data.

    Convert the raw ffprobe JSON output into a Box object with standardized fields. Extract and normalize metadata like title, format details, duration, and stream properties. Convert codec types and audio channels to enums.

    Args:
        input_path (Path): Path to the video file to probe

    Returns:
        Box: Box object containing normalized probe data with fields for format metadata and stream properties
    """
    probe_box = Box(
        run_ffprobe(input_path),
        default_box=True,
        default_box_create_on_get=False,
    )

    probe_box.path_to_file = input_path
    probe_box.name = probe_box.format.tags.title or probe_box.format.filename or input_path.name
    probe_box.format_name = probe_box.format.format_name or None
    probe_box.format_long_name = probe_box.format.format_long_name or None
    probe_box.duration = probe_box.format.duration or None
    probe_box.start_time = probe_box.format.start_time or None
    probe_box.size = probe_box.format.size or None
    probe_box.bit_rate = probe_box.format.bit_rate or None

    # Set stream codecs to enum
    for stream in probe_box.streams:
        stream.codec_type = CodecTypes(stream.codec_type.lower())
        stream.bps = stream.tags.BPS or None
        stream.title = stream.tags.title or None
        stream.channels = channels_to_layout(stream.channels)
        stream.language = stream.language or stream.tags.language or None

    return probe_box
