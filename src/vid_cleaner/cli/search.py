"""Search subcommand."""

import cappa
from nclutils import pp

from vid_cleaner.config import settings
from vid_cleaner.controllers.discovery import discover_video_files
from vid_cleaner.vidcleaner import SearchCommand
from vid_cleaner.views import search_table


def main(search_cmd: SearchCommand) -> None:
    """Search for video files under a directory.

    Args:
        search_cmd (SearchCommand): The search command instance with search-specific options

    Raises:
        cappa.Exit: Always, carrying the command's exit code.
    """
    human_readable_filters = ", ".join(f"'{f.value}'" for f in settings.filters)

    report = discover_video_files(
        search_cmd.directory,
        depth=search_cmd.depth,
        filters=set(settings.filters),
        sort=search_cmd.sort,
        reverse=search_cmd.reverse,
    )

    if report.total == 0:
        pp.warning(f"No video files found in {search_cmd.directory}")
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
        search_table(
            report.results,
            sort=report.sort,
            descending=report.descending,
            total=report.total,
            skipped=len(report.skipped),
            filtered=report.filtered,
            root=search_cmd.directory.expanduser().resolve() if search_cmd.depth > 0 else None,
        )
    )

    raise cappa.Exit(code=0)
