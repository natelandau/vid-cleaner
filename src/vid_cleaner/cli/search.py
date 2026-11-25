"""Search subcommand."""

import cappa

from vid_cleaner.cli.discovery_output import discover_from_directory, present_discovery
from vid_cleaner.controllers.discovery import sorts_descending
from vid_cleaner.vidcleaner import SearchCommand, VidCleaner, json_report_for
from vid_cleaner.views.json_output import report_on_exit, search_query, search_report_fields


def main(search_cmd: SearchCommand, vidcleaner: VidCleaner) -> None:
    """Search for video files under a directory.

    With `--json`, also print one document for the whole run to stdout.

    Args:
        search_cmd (SearchCommand): The search command instance with search-specific options
        vidcleaner (VidCleaner): The parsed CLI arguments, used to start the JSON report.

    Raises:
        cappa.Exit: Always, carrying the command's exit code.
    """
    report = json_report_for(vidcleaner)
    options = search_cmd.discovery

    query = search_query(
        root=search_cmd.directory,
        filters=set(options.filters or ()),
        sort=options.sort,
        descending=sorts_descending(options.sort, reverse=options.reverse),
        depth=options.depth,
        limit=options.limit,
    )

    with report_on_exit(report):
        if report:
            # Filled from the flags so a run that stops before discovery has the same keys
            report.summary = {"total": 0, "shown": 0, "skipped": 0, "truncated": 0}
            report.extra = {"query": query, "skipped": []}

        discovery_report = discover_from_directory(
            directory=search_cmd.directory,
            options=options,
            argument_name="The search path",
            limit=options.limit,
        )

        if report:
            # The query reports the directory discovery resolved, not the path as typed
            query["root"] = str(discovery_report.root)
            report.files, report.summary, extra = search_report_fields(discovery_report)
            report.extra = {"query": query, **extra}

        present_discovery(discovery_report)

    raise cappa.Exit(code=0)
