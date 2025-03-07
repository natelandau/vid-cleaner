"""Helper utilities."""

from pathlib import Path

import cappa
import ffmpeg as python_ffmpeg

from vid_cleaner.constants import AudioLayout

from .printer import pp


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


def ffprobe(path: Path) -> dict:  # pragma: no cover
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
