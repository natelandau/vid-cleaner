"""Discover, probe, filter, and rank video files under a directory."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nclutils import pp
from rich.live import Live

from vid_cleaner.constants import DEFAULT_PROBE_JOBS, VIDEO_SUFFIXES, SortOrder, VideoTrait
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.models import SearchResult, VideoFile
from vid_cleaner.utils import ProbeCache, is_macos_sidecar, track_progress

# Each key pairs a getter with the direction that reads naturally for it: names ascend,
# magnitudes descend. `reverse` flips whichever is active.
SortKey = Callable[[SearchResult], Any]
SORT_KEYS: dict[SortOrder, tuple[SortKey, bool]] = {
    SortOrder.ALPHA: (lambda result: str(result.video_file.path).lower(), False),
    SortOrder.SIZE: (lambda result: result.size, True),
    SortOrder.BITRATE: (lambda result: result.bitrate, True),
}


def sorts_descending(sort: SortOrder, *, reverse: bool) -> bool:
    """Report the direction a sort runs in, so a query can be described before discovery runs.

    Args:
        sort (SortOrder): The key to rank results by.
        reverse (bool): Flip the key's natural direction.

    Returns:
        bool: True when the largest or last value comes first.
    """
    return SORT_KEYS[sort][1] != reverse


@dataclass
class DiscoveryReport:
    """The outcome of one discovery run, carrying everything a caller needs to render or act.

    Hold the active query and sort state alongside the rows so a renderer's header arrow,
    caption, and error messages cannot disagree with the query that produced the rows.
    """

    root: Path
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
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _walk_video_paths(
    root: Path, *, depth: int, on_error: Callable[[OSError], None]
) -> Iterator[tuple[Path, list[Path]]]:
    """Yield each directory under `root`, down to `depth` levels, with its candidate videos.

    Decide on file names alone before touching the filesystem, so the sidecars, artwork,
    and subtitles that fill a media library cost no `stat()` on a slow network share.

    Args:
        root (Path): Directory to start from.
        depth (int): Subdirectory levels to descend. 0 lists only `root`.
        on_error (Callable[[OSError], None]): Receives the error for each directory that
            cannot be listed, so the caller can report it instead of losing its files.

    Yields:
        tuple[Path, list[Path]]: A directory and the video files directly inside it.
    """
    # Shallowest level each real directory was walked at, keyed by resolved path.
    shallowest_level: dict[Path, int] = {}

    # `os.walk` rather than `Path.walk`, which needs Python 3.12. Following links keeps
    # symlinked folders in a library searchable.
    for current, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=True):
        directory = Path(current)
        level = len(directory.relative_to(root).parts)

        # A link back to an ancestor would otherwise multiply the walk at every level.
        # Walk a directory again only when reached nearer the root, where more of its
        # subdirectories fall within `depth`.
        real_directory = directory.resolve()
        if shallowest_level.get(real_directory, depth + 1) <= level:
            dirnames[:] = []
            continue
        shallowest_level[real_directory] = level

        if level >= depth:
            dirnames[:] = []
        # Compare suffixes rather than globbing, since glob is case-sensitive on POSIX and
        # would miss `MOVIE.MKV`.
        yield (
            directory,
            [
                directory / name
                for name in filenames
                if Path(name).suffix.lower() in VIDEO_SUFFIXES and not is_macos_sidecar(Path(name))
            ],
        )


def _collect_video_files(
    root: Path, *, depth: int, unreadable: list[VideoProbeError]
) -> list[VideoFile]:
    """Find every video file under `root` once, showing progress while the walk runs.

    Args:
        root (Path): Directory to search.
        probe_cache (ProbeCache): Cache of earlier ffprobe results. `ProbeCache(None)`
            probes every file.
        depth (int): Subdirectory levels to descend. 0 searches only `root`.
        unreadable (list[VideoProbeError]): Receives each directory or file that could
            not be read, so the caller reports it alongside the files that failed to probe.

    Returns:
        list[VideoFile]: One entry per distinct file, unprobed.
    """

    def record_unlistable_directory(error: OSError) -> None:
        unreadable.append(
            VideoProbeError(
                path=Path(error.filename),
                reason=f"directory could not be listed: {error.strerror or error}",
            )
        )

    # Keyed by resolved path so a file reached twice, such as through a symlinked
    # subdirectory, is probed, listed, and cleaned once.
    video_files_by_path: dict[Path, VideoFile] = {}
    directories_searched = 0
    with Live(console=pp.console(), auto_refresh=True) as live:
        for directories_searched, (_, paths) in enumerate(
            _walk_video_paths(root, depth=depth, on_error=record_unlistable_directory), start=1
        ):
            for path in paths:
                # Only a regular file can hold a video. A FIFO would block ffprobe, and
                # before Python 3.13 `resolve()` raises on a symlink loop.
                try:
                    if not path.is_file():
                        continue
                except OSError as e:
                    unreadable.append(VideoProbeError(path=path, reason=str(e)))
                    continue

                # Not `coerce_video_files`: its `cappa.Exit` for a file deleted mid-scan would
                # end the whole run instead of skipping that file at probe time.
                video_file = VideoFile(path)
                video_files_by_path.setdefault(video_file.path, video_file)
            live.update(
                f"Found {len(video_files_by_path)} video files in {directories_searched} directories"
            )

        live.update(
            f"[dim]Found {len(video_files_by_path)} video files in {directories_searched} directories[/dim]"
        )
        live.stop()

    return list(video_files_by_path.values())


def _read_search_result(
    video_file: VideoFile, *, filters: set[VideoTrait], probe_cache: ProbeCache
) -> SearchResult | None:
    """Probe one file and describe it, or report that it does not match the filters.

    Runs on a worker thread, so it touches nothing shared but the thread-safe cache.

    Args:
        video_file (VideoFile): The file to probe.
        filters (set[VideoTrait]): Traits the file must have all of.
        probe_cache (ProbeCache): Cache of earlier ffprobe results.

    Returns:
        SearchResult | None: The file's row, or None when it lacks a required trait.

    Raises:
        VideoProbeError: If the file cannot be probed or vanished before its size was read.
    """
    video_file.load_probe(probe_cache)
    video_traits = video_file.get_traits()
    if not filters.issubset(video_traits):
        return None

    # A file that vanishes between the probe and this stat() (e.g. deleted by another
    # process mid-scan) must not abort a run that may have already spent minutes probing
    # the rest of the library.
    try:
        size = video_file.path.stat().st_size
    except OSError as e:
        raise VideoProbeError(path=video_file.path, reason=str(e)) from e

    return SearchResult(
        video_file=video_file,
        traits=video_traits,
        matches=[trait for trait in video_traits if trait in filters],
        size=size,
        bitrate=coerce_bitrate(video_file.probe_box.bit_rate),
    )


def _probe_in_parallel(
    video_files: list[VideoFile],
    *,
    filters: set[VideoTrait],
    probe_cache: ProbeCache,
    jobs: int,
    description: str,
) -> tuple[list[SearchResult], list[VideoProbeError]]:
    """Probe files on a pool of threads, since ffprobe spends most of its time waiting on reads.

    Args:
        video_files (list[VideoFile]): The files to probe.
        filters (set[VideoTrait]): Traits a file must have all of.
        probe_cache (ProbeCache): Cache of earlier ffprobe results.
        jobs (int): The most files to probe at once.
        description (str): The label beside the progress bar.

    Returns:
        tuple[list[SearchResult], list[VideoProbeError]]: The matching files, in
            completion order, and the unreadable files, sorted by path.
    """
    results: list[SearchResult] = []
    unreadable: list[VideoProbeError] = []
    executor = ThreadPoolExecutor(max_workers=jobs)
    try:
        futures: list[Future[SearchResult | None]] = [
            executor.submit(
                _read_search_result, video_file, filters=filters, probe_cache=probe_cache
            )
            for video_file in video_files
        ]
        for future in track_progress(
            as_completed(futures), description=description, transient=True, total=len(futures)
        ):
            # A video extension is no guarantee of video content; corrupt or truncated
            # files must not abort the whole run.
            try:
                result = future.result()
            except VideoProbeError as e:
                unreadable.append(e)
                continue
            if result is not None:
                results.append(result)
    finally:
        # On Ctrl-C, drop the queued files rather than waiting for the whole library to
        # finish probing. The few ffprobes already running end on their own.
        executor.shutdown(wait=False, cancel_futures=True)

    # Completion order varies run to run; the report should not
    unreadable.sort(key=lambda error: str(error.path))
    return results, unreadable


def discover_video_files(  # noqa: PLR0913
    root: Path,
    *,
    probe_cache: ProbeCache,
    depth: int = 0,
    filters: set[VideoTrait] | None = None,
    sort: SortOrder = SortOrder.ALPHA,
    reverse: bool = False,
    limit: int | None = None,
    jobs: int = DEFAULT_PROBE_JOBS,
) -> DiscoveryReport:
    """Find, probe, filter, and rank the video files under a directory.

    Use this wherever a command needs to act on a set of files the user described by
    query rather than by path, so `search` and `clean` always select identically.

    Args:
        root (Path): Directory to search.
        probe_cache (ProbeCache): Cache of earlier ffprobe results. `ProbeCache(None)`
            probes every file.
        depth (int): Subdirectory levels to descend. 0 searches only `root`.
        filters (set[VideoTrait] | None): Traits a file must have all of. None or an empty
            set keeps every readable file.
        sort (SortOrder): Key to rank results by.
        reverse (bool): Flip the key's natural direction.
        limit (int | None): Keep only the first N results after sorting.
        jobs (int): The most files to probe at once.

    Returns:
        DiscoveryReport: The ranked selection plus the counts needed to describe it.
    """
    active_filters = filters or set()
    human_readable_filters = " and ".join(f"'{f}'" for f in sorted(active_filters))

    unreadable: list[VideoProbeError] = []
    video_files = _collect_video_files(root, depth=depth, unreadable=unreadable)

    results, unprobeable = _probe_in_parallel(
        video_files,
        filters=active_filters,
        probe_cache=probe_cache,
        jobs=jobs,
        description=(
            f"Filtering {len(video_files)} files for {human_readable_filters}..."
            if active_filters
            else f"Reading {len(video_files)} files..."
        ),
    )
    unreadable.extend(unprobeable)

    # Presort by path so ties on the active key (e.g. every bitrate-less file) resolve
    # deterministically instead of reflecting the order probes finished in. `sort` is
    # stable, so this ordering survives as the tiebreaker after the real sort below.
    results.sort(key=SORT_KEYS[SortOrder.ALPHA][0])

    key_fn, _ = SORT_KEYS[sort]
    descending = sorts_descending(sort, reverse=reverse)
    results.sort(key=key_fn, reverse=descending)

    truncated = 0
    if limit is not None and limit < len(results):
        truncated = len(results) - limit
        results = results[:limit]

    return DiscoveryReport(
        root=root,
        results=results,
        total=len(video_files),
        skipped=unreadable,
        truncated=truncated,
        sort=sort,
        descending=descending,
        filters=active_filters,
        depth=depth,
    )
