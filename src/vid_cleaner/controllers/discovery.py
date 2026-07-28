"""Discover, probe, filter, and rank video files under a directory."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import Any

from nclutils import pp
from nclutils.fs import find_files, find_subdirectories
from rich.live import Live
from rich.progress import track

from vid_cleaner.constants import SortOrder, VideoContainerTypes, VideoTrait
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.models import SearchResult
from vid_cleaner.utils import coerce_video_files

# Each key pairs a getter with the direction that reads naturally for it: names ascend,
# magnitudes descend. `reverse` flips whichever is active.
SortKey = Callable[[SearchResult], Any]
SORT_KEYS: dict[SortOrder, tuple[SortKey, bool]] = {
    SortOrder.ALPHA: (lambda result: str(result.video_file.path).lower(), False),
    SortOrder.SIZE: (lambda result: result.size, True),
    SortOrder.BITRATE: (lambda result: result.bitrate, True),
}


@dataclass
class DiscoveryReport:
    """The outcome of one discovery run, carrying everything a caller needs to render or act.

    Hold the active query and sort state alongside the rows so a renderer's header arrow,
    caption, and error messages cannot disagree with the query that produced the rows.
    """

    results: list[SearchResult]
    total: int
    skipped: list[VideoProbeError]
    truncated: int
    sort: SortOrder
    descending: bool
    filters: set[VideoTrait]
    depth: int

    @property
    def filtered(self) -> bool:
        """Report whether any trait filter narrowed the selection.

        Returns:
            bool: True when at least one filter was applied.
        """
        return bool(self.filters)

    @property
    def recursive(self) -> bool:
        """Report whether the search descended below its root.

        Returns:
            bool: True when subdirectories were searched.
        """
        return self.depth > 0


def coerce_bitrate(raw: str | int | None) -> int:
    """Convert an ffprobe bit_rate value to an integer, falling back to zero.

    ffprobe reports bit_rate as a string and omits it entirely for some containers, so
    sorting needs a total order that a missing value cannot break.

    Args:
        raw (str | int | None): The `bit_rate` value from a probe box.

    Returns:
        int: The bitrate in bits per second, or 0 when it is missing or unparsable.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def discover_video_files(
    root: Path,
    *,
    depth: int = 0,
    filters: set[VideoTrait] | None = None,
    sort: SortOrder = SortOrder.ALPHA,
    reverse: bool = False,
    limit: int | None = None,
) -> DiscoveryReport:
    """Find, probe, filter, and rank the video files under a directory.

    Use this wherever a command needs to act on a set of files the user described by
    query rather than by path, so `search` and `clean` always select identically.

    Args:
        root (Path): Directory to search.
        depth (int): Subdirectory levels to descend. 0 searches only `root`.
        filters (set[VideoTrait] | None): Traits a file must have at least one of. None or
            an empty set keeps every readable file.
        sort (SortOrder): Key to rank results by.
        reverse (bool): Flip the key's natural direction.
        limit (int | None): Keep only the first N results after sorting.

    Returns:
        DiscoveryReport: The ranked selection plus the counts needed to describe it.
    """
    active_filters = filters or set()
    human_readable_filters = ", ".join(f"'{f.value}'" for f in active_filters)

    directories_to_search = [*find_subdirectories(root, depth=depth), root] if depth > 0 else [root]

    video_files = []
    with Live(console=pp.console(), auto_refresh=True) as live:
        for i, directory in enumerate(directories_to_search):
            video_files.extend(
                coerce_video_files(
                    find_files(
                        directory,
                        globs=[
                            f"*{container_type.value}" for container_type in VideoContainerTypes
                        ],
                    )
                )
            )
            live.update(
                f"Found {len(video_files)} video files in {i + 1}/{len(directories_to_search)} directories"
            )

        live.update(
            f"[dim]Found {len(video_files)} video files in {i + 1}/{len(directories_to_search)} directories[/dim]"
        )
        live.stop()

    results: list[SearchResult] = []
    unreadable: list[VideoProbeError] = []
    for video_file in track(
        video_files,
        description=f"Filtering {len(video_files)} files for {human_readable_filters}...",
        transient=True,
    ):
        # A video extension is no guarantee of video content; corrupt files and stubs like
        # AppleDouble `._` sidecars must not abort the whole run.
        try:
            video_traits = video_file.get_traits()
        except VideoProbeError as e:
            unreadable.append(e)
            continue

        matches = [trait for trait in video_traits if trait in active_filters]
        if active_filters and not matches:
            continue

        # A file that vanishes between the probe above and this stat() (e.g. deleted by
        # another process mid-scan) must not abort a run that may have already spent
        # minutes probing the rest of the library.
        try:
            size = video_file.path.stat().st_size
        except OSError as e:
            unreadable.append(VideoProbeError(path=video_file.path, reason=str(e)))
            continue

        results.append(
            SearchResult(
                video_file=video_file,
                traits=video_traits,
                matches=matches,
                size=size,
                bitrate=coerce_bitrate(video_file.probe_box.bit_rate),
            )
        )

    # Presort by path so ties on the active key (e.g. every bitrate-less file) resolve
    # deterministically instead of reflecting find_files' arbitrary scan order. `sort`
    # is stable, so this ordering survives as the tiebreaker after the real sort below.
    results.sort(key=SORT_KEYS[SortOrder.ALPHA][0])

    key_fn, descends_by_default = SORT_KEYS[sort]
    descending = descends_by_default != reverse
    results.sort(key=key_fn, reverse=descending)

    truncated = 0
    if limit is not None and limit < len(results):
        truncated = len(results) - limit
        results = results[:limit]

    return DiscoveryReport(
        results=results,
        total=len(video_files),
        skipped=unreadable,
        truncated=truncated,
        sort=sort,
        descending=descending,
        filters=active_filters,
        depth=depth,
    )
