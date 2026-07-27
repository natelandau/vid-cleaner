"""Search subcommand."""

from collections.abc import Callable
from typing import Any

import cappa
from nclutils import pp
from nclutils.fs import find_files, find_subdirectories
from rich.live import Live
from rich.progress import track

from vid_cleaner.config import settings
from vid_cleaner.constants import SortOrder, VideoContainerTypes
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.models import SearchResult
from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import SearchCommand
from vid_cleaner.views import search_table

# Each key pairs a getter with the direction that reads naturally for it: names ascend,
# magnitudes descend. `--reverse` flips whichever is active.
SortKey = Callable[[SearchResult], Any]
SORT_KEYS: dict[SortOrder, tuple[SortKey, bool]] = {
    SortOrder.ALPHA: (lambda result: str(result.video_file.path).lower(), False),
    SortOrder.SIZE: (lambda result: result.size, True),
    SortOrder.BITRATE: (lambda result: result.bitrate, True),
}


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


def main(search_cmd: SearchCommand) -> None:
    """Search for video files under a directory.

    Args:
        search_cmd (SearchCommand): The search command instance with search-specific options

    Raises:
        cappa.Exit: If no video files are found
    """
    human_readable_filters = ", ".join(f"'{f.value}'" for f in settings.filters)

    directories_to_search = (
        [*find_subdirectories(search_cmd.directory, depth=search_cmd.depth), search_cmd.directory]
        if search_cmd.depth > 0
        else [search_cmd.directory]
    )

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

    if len(video_files) == 0:
        pp.warning(f"No video files found in {search_cmd.directory}")
        raise cappa.Exit(code=0)

    results: list[SearchResult] = []
    unreadable: list[VideoProbeError] = []
    for video_file in track(
        video_files,
        description=f"Filtering {len(video_files)} files for {human_readable_filters}...",
        transient=True,
    ):
        # A video extension is no guarantee of video content; corrupt files and stubs like
        # AppleDouble `._` sidecars must not abort the whole search.
        try:
            video_traits = video_file.get_traits()
        except VideoProbeError as e:
            unreadable.append(e)
            continue

        matches = [trait for trait in video_traits if trait in settings.filters]
        if settings.filters and not matches:
            continue

        # A file that vanishes between the probe above and this stat() (e.g. deleted by
        # another process mid-scan) must not abort a batch search that may have already
        # spent minutes probing the rest of the library.
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

    if unreadable:
        pp.debug(
            "Unreadable files", details=[f"{e.path}: {e.reason}" for e in unreadable], markup=False
        )

    if not results:
        # The table caption is the sole reporter of the skipped count on the success
        # path, but it never renders here, so fold the count into the error instead of
        # leaving the user unable to tell "nothing matches" from "nothing was readable".
        message = (
            f"No video files found matching {human_readable_filters}"
            if human_readable_filters
            else "No video files could be read"
        )
        if unreadable:
            message += f" ({len(unreadable)} file(s) skipped as unreadable)"
        pp.error(message)
        raise cappa.Exit(code=1)

    # Presort by path so ties on the active key (e.g. every bitrate-less file) resolve
    # deterministically instead of reflecting find_files' arbitrary scan order. `sort`
    # is stable, so this ordering survives as the tiebreaker after the real sort below.
    results.sort(key=SORT_KEYS[SortOrder.ALPHA][0])

    key_fn, descends_by_default = SORT_KEYS[search_cmd.sort]
    descending = descends_by_default != search_cmd.reverse
    results.sort(key=key_fn, reverse=descending)

    pp.console().print(
        search_table(
            results,
            sort=search_cmd.sort,
            descending=descending,
            total=len(video_files),
            skipped=len(unreadable),
            filtered=bool(settings.filters),
            root=search_cmd.directory.expanduser().resolve() if search_cmd.depth > 0 else None,
        )
    )

    raise cappa.Exit(code=0)
