"""Constants for video-transcode."""

from enum import Enum
from pathlib import Path

import typer


class LogLevel(Enum):
    """Log levels for video-transcode."""

    INFO = 0
    DEBUG = 1
    TRACE = 2
    WARNING = 3
    ERROR = 4


class AudioLayout(Enum):
    """Audio layouts for video-transcode. Values are the number of streams."""

    MONO = 1
    STEREO = 2
    SURROUND5 = 6
    SURROUND7 = 8


APP_DIR = Path(typer.get_app_dir("video-transcode"))
EXCLUDED_VIDEO_CODECS = {"mjpeg", "png"}
FFMPEG_APPEND: list[str] = ["-max_muxing_queue_size", "9999"]
FFMPEG_PREPEND: list[str] = ["-y", "-hide_banner"]
H265_CODECS = {"hevc", "vp9"}
