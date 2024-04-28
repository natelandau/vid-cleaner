"""Constants for vid-cleaner."""

from enum import Enum
from pathlib import Path

import typer


class VideoContainerTypes(str, Enum):
    """Video container types for vid-cleaner."""

    MKV = ".mkv"
    MP4 = ".mp4"
    AVI = ".avi"
    WEBM = ".webm"
    MOV = ".mov"
    WMV = ".wmv"
    M4V = ".m4v"


class CodecTypes(str, Enum):
    """Codec types for vid-cleaner."""

    AUDIO = "audio"
    VIDEO = "video"
    SUBTITLE = "subtitle"


class AudioLayout(Enum):
    """Audio layouts for vid-cleaner. Values are the number of streams."""

    MONO = 1
    STEREO = 2
    SURROUND5 = 6
    SURROUND7 = 8


SYMBOL_CHECK = "✔"

APP_DIR = Path(typer.get_app_dir("vid-cleaner"))
CONFIG_PATH = APP_DIR / "config.toml"
EXCLUDED_VIDEO_CODECS = {"mjpeg", "mjpg", "png"}
FFMPEG_APPEND: list[str] = ["-max_muxing_queue_size", "9999"]
FFMPEG_PREPEND: list[str] = ["-y", "-hide_banner"]
H265_CODECS = {"hevc", "vp9"}
VERSION = "0.3.2"

# how many bytes to read at once?
# shutil.copy uses 1024 * 1024 if _WINDOWS else 64 * 1024
# however, in my testing on MacOS with SSD, I've found a much larger buffer is faster
BUFFER_SIZE = 4096 * 1024
