"""Search result model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vid_cleaner.constants import VideoTrait
    from vid_cleaner.models.video_file import VideoFile


@dataclass
class SearchResult:
    """A probed video file that survived the active trait filters.

    Carry everything the results table needs so rendering never has to re-probe a file
    or touch the filesystem again.
    """

    video_file: VideoFile
    traits: list[VideoTrait]
    matches: list[VideoTrait]
    size: int
    bitrate: int
