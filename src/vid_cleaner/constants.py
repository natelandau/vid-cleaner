"""Constants for vid-cleaner."""

import os
from enum import Enum
from pathlib import Path

PACKAGE_NAME = __package__.replace("_", "-").replace(".", "-").replace(" ", "-")
CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", "~/.config")).expanduser().absolute() / PACKAGE_NAME
DATA_DIR = Path(os.getenv("XDG_DATA_HOME", "~/.local/share")).expanduser().absolute() / PACKAGE_NAME
STATE_DIR = (
    Path(os.getenv("XDG_STATE_HOME", "~/.local/state")).expanduser().absolute() / PACKAGE_NAME
)
CACHE_DIR = Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser().absolute() / PACKAGE_NAME
PROJECT_ROOT_PATH = Path(__file__).parents[2].absolute()
PACKAGE_ROOT_PATH = Path(__file__).parents[0].absolute()
USER_CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_CONFIG_PATH = PACKAGE_ROOT_PATH / "default_config.toml"
DEV_DIR = PROJECT_ROOT_PATH / ".development"
DEV_CONFIG_PATH = DEV_DIR / "dev-config.toml"

COMMENTARY_STREAM_TITLE_REGEX = r"commentary|sdh|description"


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
    ATTACHMENT = "attachment"
    DATA = "data"


class AudioLayout(Enum):
    """Audio layouts for vid-cleaner. Values are the number of streams."""

    MONO = 1
    STEREO = 2
    SURROUND5 = 6
    SURROUND7 = 8


SYMBOL_CHECK = "✔"

EXCLUDED_VIDEO_CODECS = {"mjpeg", "mjpg", "png"}
FFMPEG_APPEND: list[str] = ["-max_muxing_queue_size", "9999"]
FFMPEG_PREPEND: list[str] = ["-y", "-hide_banner"]
H265_CODECS = {"hevc", "vp9"}
VERSION = "0.4.0"

# how many bytes to read at once?
# shutil.copy uses 1024 * 1024 if _WINDOWS else 64 * 1024
# however, in my testing on MacOS with SSD, I've found a much larger buffer is faster
IO_BUFFER_SIZE = 4096 * 1024


class PrintLevel(Enum):
    """Define verbosity levels for console output.

    Use these levels to control the amount of information displayed to users. Higher levels include all information from lower levels plus additional details.
    """

    INFO = 0
    DEBUG = 1
    TRACE = 2
