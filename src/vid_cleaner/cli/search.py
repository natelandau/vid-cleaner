"""Search subcommand."""

import cappa

from vid_cleaner.cli.discovery_output import discover_from_directory, present_discovery
from vid_cleaner.vidcleaner import SearchCommand


def main(search_cmd: SearchCommand) -> None:
    """Search for video files under a directory.

    Args:
        search_cmd (SearchCommand): The search command instance with search-specific options

    Raises:
        cappa.Exit: Always, carrying the command's exit code.
    """
    report = discover_from_directory(
        search_cmd.directory,
        search_cmd.discovery,
        argument_name="The search path",
        limit=search_cmd.discovery.limit,
    )

    present_discovery(report)

    raise cappa.Exit(code=0)
