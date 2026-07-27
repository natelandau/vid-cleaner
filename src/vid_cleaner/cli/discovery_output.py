"""Shared presentation of discovery results for `search` and `clean`.

Keep this the only caller of `search_table` so the selection `clean` previews is the
same rendering `search` prints, by construction rather than by convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cappa
from nclutils import pp

from vid_cleaner.views import search_table

if TYPE_CHECKING:
    from pathlib import Path

    from vid_cleaner.constants import VideoTrait
    from vid_cleaner.controllers.discovery import DiscoveryReport


def present_discovery(
    report: DiscoveryReport,
    *,
    root: Path,
    filters: set[VideoTrait],
    recursive: bool,
) -> None:
    """Render a discovery report, or exit with the message its emptiness calls for.

    Distinguish "the directory holds no video files" (not an error) from "files were
    found but none matched" (an error), so a user can tell a bad path from a bad filter.

    Args:
        report (DiscoveryReport): The selection to present.
        root (Path): The directory that was searched, named in the empty-directory warning.
        filters (set[VideoTrait]): The active trait filters, named in the no-match error.
        recursive (bool): Whether the search descended, so rows can show their directory.

    Raises:
        cappa.Exit: With code 0 when no video files exist under `root`, or code 1 when
            files were found but none survived the filters.
    """
    if report.total == 0:
        pp.warning(f"No video files found in {root}")
        raise cappa.Exit(code=0)

    if report.skipped:
        pp.debug(
            "Unreadable files",
            details=[f"{e.path}: {e.reason}" for e in report.skipped],
            markup=False,
        )

    if not report.results:
        # The table caption is the sole reporter of the skipped count on the success
        # path, but it never renders here, so fold the count into the error instead of
        # leaving the user unable to tell "nothing matches" from "nothing was readable".
        human_readable_filters = ", ".join(f"'{f.value}'" for f in filters)
        message = (
            f"No video files found matching {human_readable_filters}"
            if human_readable_filters
            else "No video files could be read"
        )
        if report.skipped:
            message += f" ({len(report.skipped)} file(s) skipped as unreadable)"
        pp.error(message)
        raise cappa.Exit(code=1)

    pp.console().print(
        search_table(report, root=root.expanduser().resolve() if recursive else None)
    )
