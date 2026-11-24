"""Search subcommand."""

import cappa

from vid_cleaner.cli.discovery_output import present_discovery, resolve_discovery_root
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
    root = resolve_discovery_root(search_cmd.directory, argument_name="The search path")
    report = discover_video_files(
        root,
        depth=search_cmd.discovery.depth,
        filters=set(settings.filters),
        sort=search_cmd.discovery.sort,
        reverse=search_cmd.discovery.reverse,
        limit=search_cmd.discovery.limit,
    )

    present_discovery(report, root=root)

    raise cappa.Exit(code=0)
