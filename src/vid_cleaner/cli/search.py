"""Search subcommand."""

import cappa
from nclutils import console, find_files, find_subdirectories
from rich.live import Live
from rich.progress import track
from rich.table import Table

from vid_cleaner.config import settings
from vid_cleaner.constants import VideoContainerTypes
from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import SearchCommand


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

    with Live(console=console, auto_refresh=True) as live:
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
        console.print(f"No video files found in {search_cmd.directory}")
        raise cappa.Exit(code=0)

    table = Table(title="Video Files")
    table.add_column("#")
    table.add_column("Name")
    table.add_column("Matching filters")
    table.add_column("Video info")

    i = 0
    for video_file in track(
        video_files,
        description=f"Filtering {len(video_files)} files for {human_readable_filters}...",
        transient=True,
    ):
        video_traits = video_file.get_traits()

        matches = [trait for trait in video_traits if trait in settings.filters]
        if settings.filters and not matches:
            continue

        i += 1
        table.add_row(str(i), video_file.name, ", ".join(matches), ", ".join(video_traits))

    if i == 0:
        console.print(f"No video files found matching {human_readable_filters}")
        raise cappa.Exit(code=1)

    console.print(table)

    raise cappa.Exit(code=0)
