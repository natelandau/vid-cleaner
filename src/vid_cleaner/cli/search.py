"""Search subcommand."""

import cappa

from vid_cleaner.cli.discovery_output import present_discovery
from vid_cleaner.config import settings
from vid_cleaner.controllers.discovery import discover_video_files
from vid_cleaner.vidcleaner import SearchCommand


def main(search_cmd: SearchCommand) -> None:
    """Search for video files under a directory.

    Args:
        search_cmd (SearchCommand): The search command instance with search-specific options

    Raises:
        cappa.Exit: Always, carrying the command's exit code.
    """
    filters = set(settings.filters)

    report = discover_video_files(
        search_cmd.directory,
        depth=search_cmd.discovery.depth,
        filters=filters,
        sort=search_cmd.discovery.sort,
        reverse=search_cmd.discovery.reverse,
        limit=search_cmd.discovery.limit,
    )

    present_discovery(
        report,
        root=search_cmd.directory,
        filters=filters,
        recursive=search_cmd.discovery.depth > 0,
    )

    raise cappa.Exit(code=0)
