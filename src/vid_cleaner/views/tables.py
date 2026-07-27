"""Tables for the VidCleaner application."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.filesize import decimal
from rich.table import Table
from rich.text import Text

from vid_cleaner.constants import SortOrder

if TYPE_CHECKING:
    from box import Box

    from vid_cleaner.constants import VideoTrait
    from vid_cleaner.controllers.discovery import DiscoveryReport

# Scene releases lead with the title and switch to release metadata at the year (movies) or
# the season/episode token (TV). Everything after that point restates, in scene shorthand,
# what the traits line already says in normalized form.
TITLE_END_RE = re.compile(r"\b((?:19|20)\d{2}|S\d{2}E\d{2})\b", re.IGNORECASE)


def format_size(size: int) -> Text:
    """Render a byte count with the magnitude prominent and the unit dim.

    Args:
        size (int): File size in bytes.

    Returns:
        Text: Styled size, e.g. `51.8 GB`.
    """
    magnitude, _, unit = decimal(size).rpartition(" ")
    text = Text(magnitude)
    text.append(f" {unit}", style="dim")
    return text


def format_bitrate(bitrate: int) -> Text:
    """Render a bitrate in megabits per second, or a dash when it is unknown.

    Args:
        bitrate (int): Bitrate in bits per second; 0 means ffprobe did not report one.

    Returns:
        Text: Styled bitrate, e.g. `24.3 Mb/s`, or a dim `--`.
    """
    if not bitrate:
        return Text("--", style="dim")

    text = Text(f"{bitrate / 1_000_000:.1f}")
    text.append(" Mb/s", style="dim")
    return text


def format_name(filename: str, *, parent: str = "") -> Text:
    """Render a filename with the title emphasized over its release metadata.

    Keep the suffix visible because the traits line says nothing about the container, and
    keep dots inside the metadata tail because tokens like `DTS-HD.MA.7.1` depend on them.

    Args:
        filename (str): The file name, including its suffix.
        parent (str): A directory path to render as a dim segment ahead of the filename,
            e.g. so a recursive search's results stay grouped by directory. Omitted
            entirely when empty.

    Returns:
        Text: Styled name with a bold title and a dim metadata tail.
    """
    stem, _, suffix = filename.rpartition(".")
    if not stem:  # No suffix to split off.
        stem, suffix = filename, ""

    # AppleDouble sidecar files (`._name`) and genuinely hidden dotfiles (`.private`,
    # `..hidden`) carry leading dots that are not a scene-release separator. Split them
    # off and keep them verbatim (never space-replaced or stripped) so a hidden file's
    # rendered name round-trips exactly to the name on disk.
    leading_dots = stem[: len(stem) - len(stem.lstrip("."))]
    stem = stem[len(leading_dots) :]

    # Only the LAST year/SxxEyy token can end the title: a movie year is often followed
    # by a resolution that also starts with two digits, and a first-match search would
    # wrongly stop at that leading number instead of the real title/metadata boundary.
    matches = list(TITLE_END_RE.finditer(stem))
    match = matches[-1] if matches else None

    # Build by appending rather than via `Text(..., style=...)`: the constructor sets a base
    # style on the whole object instead of a span, which erases the title/tail distinction
    # once the cell is composed with others.
    text = Text()
    if parent:
        text.append(f"{parent}/", style="dim")

    # A dot immediately after the match is a real scene-release separator to bridge with a
    # space. Any other boundary (a parenthesis, a space, or nothing) was never a separator,
    # so the whole stem renders as the title instead of manufacturing a tail from it.
    if match and stem[match.end() : match.end() + 1] == ".":
        text.append(leading_dots + stem[: match.end()].replace(".", " "), style="bold")
        # The dot right after the match was the only thing to bridge; if nothing
        # follows it, there is no tail left to append and no separator to emit.
        tail = stem[match.end() + 1 :]
        if tail:
            text.append(" " + tail, style="dim")
    else:
        text.append(leading_dots + stem.replace(".", " "), style="bold")

    if suffix:
        text.append(f".{suffix}", style="dim")

    return text


def format_traits(traits: list[VideoTrait], matches: list[VideoTrait]) -> Text:
    """Render a trait list with the filter matches lifted out of the dim background.

    Args:
        traits (list[VideoTrait]): Every trait detected on the file, in display order.
        matches (list[VideoTrait]): The subset that matched the active filters.

    Returns:
        Text: Styled trait line separated by middots.
    """
    matched = set(matches)
    text = Text()
    for index, trait in enumerate(traits):
        if index:
            text.append(" · ", style="dim")
        text.append(trait.value, style="" if trait in matched else "dim")

    return text


def sort_header(label: str, *, active: bool, descending: bool) -> Text:
    """Render a column header, marking the active sort key with a direction arrow.

    The arrow reports the direction values actually run in, not which flag produced it:
    `↓` for high-to-low (or Z-to-A), `↑` for the reverse.

    Args:
        label (str): The column name.
        active (bool): Whether this column is the active sort key.
        descending (bool): Whether values run from largest to smallest down the column.

    Returns:
        Text: Styled header text.
    """
    if not active:
        return Text(label, style="dim")

    return Text(f"{label} {'↓' if descending else '↑'}", style="bold cyan")


def search_table(report: DiscoveryReport, *, root: Path | None = None) -> Table:
    """Build the discovery results table shared by `search` and `clean`.

    Stack each file's traits beneath its name inside one cell so the filename gets the
    table's full width instead of competing with a traits column, and so filter matches
    can be shown as emphasis rather than as a second column repeating the same tokens.

    Args:
        report (DiscoveryReport): The ranked selection and the counts describing it.
        root (Path | None): The directory a recursive search started from. When set,
            each row's directory renders as a dim segment ahead of its filename so
            same-named files in different directories stay distinguishable. Omitted
            for a non-recursive (depth 0) search.

    Returns:
        Table: Rich table ready to print.
    """
    matched = len(report.results) + report.truncated
    summary = (
        f"{matched} of {report.total} files matched" if report.filtered else f"{matched} files"
    )

    parts = [summary]
    if report.truncated:
        parts.insert(0, f"showing {len(report.results)}")
    if report.skipped:
        parts.append(f"{len(report.skipped)} skipped")
    caption = " · ".join(parts)

    table = Table(
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        show_edge=False,
        caption=caption,
        caption_style="dim",
        caption_justify="left",
        # Rich's default header_style ("table.header" = bold) would otherwise combine
        # with each inactive header's own "dim" style, rendering bold+dim instead of
        # plain dim.
        header_style="",
    )
    table.add_column("#", justify="right", style="dim", header_style="dim", no_wrap=True)
    # Fold rather than truncate: a clipped filename cannot identify a file.
    table.add_column(
        sort_header("File", active=report.sort == SortOrder.ALPHA, descending=report.descending),
        overflow="fold",
    )
    table.add_column(
        sort_header("Size", active=report.sort == SortOrder.SIZE, descending=report.descending),
        justify="right",
        no_wrap=True,
    )
    table.add_column(
        sort_header(
            "Bitrate", active=report.sort == SortOrder.BITRATE, descending=report.descending
        ),
        justify="right",
        no_wrap=True,
    )

    for index, result in enumerate(report.results, start=1):
        parent = ""
        if root is not None:
            try:
                relative_dir = result.video_file.path.parent.relative_to(root)
            except ValueError:
                # A result outside the search root (should not happen in practice)
                # falls back to the bare filename rather than raising.
                relative_dir = Path()
            if relative_dir != Path():
                parent = relative_dir.as_posix()

        # Take the filename from the same resolved path the directory segment came from,
        # so a symlinked file never renders its link name under its target's directory.
        name_cell = format_name(result.video_file.path.name, parent=parent)
        name_cell.append("\n")
        name_cell.append(format_traits(result.traits, result.matches))
        table.add_row(
            str(index),
            name_cell,
            format_size(result.size),
            format_bitrate(result.bitrate),
        )

    return table


def stream_table(ffprobe_box: Box) -> Table:
    """Create a formatted table displaying video stream information.

    Display details about video, audio and subtitle streams in a terminal-friendly format. The table includes stream index, codec type, language, audio channels, dimensions and titles.

    Args:
        ffprobe_box (Box): Box object containing ffprobe output with stream information

    Returns:
        Table: Rich table containing formatted stream information
    """
    # Read the size from disk rather than ffprobe's `format.size`, which is a string and is
    # absent for some containers, so both views always report the same number.
    size = format_size(ffprobe_box.path_to_file.stat().st_size).plain
    table = Table(title=f"{ffprobe_box.name} ({size})")
    table.add_column("#")
    table.add_column("Type")
    table.add_column("Codec Name")
    table.add_column("Language")
    table.add_column("Channels")
    table.add_column("Channel Layout")
    table.add_column("Width")
    table.add_column("Height")
    table.add_column("Title")

    for stream in ffprobe_box.streams:
        table.add_row(
            str(stream.index),
            stream.codec_type.value,
            stream.codec_name or "",
            stream.language or "",
            str(stream.channels.value) if stream.channels else "",
            stream.channel_layout or "",
            str(stream.width) if stream.width else "",
            str(stream.height) if stream.height else "",
            stream.title or "",
        )

    return table
