# type: ignore
"""Test the table render helpers."""

import pytest
from rich.console import Console
from rich.table import Table

from vid_cleaner.constants import VideoTrait
from vid_cleaner.views.tables import (
    format_bitrate,
    format_name,
    format_size,
    format_traits,
    sort_header,
)


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "0 bytes"), (84_000_000, "84.0 MB"), (51_800_000_000, "51.8 GB")],
)
def test_format_size_renders_human_readable(size, expected):
    """Verify byte counts render as human readable magnitudes."""
    # Given/When: Formatting a byte count
    result = format_size(size)

    # Then: The text reads as a human readable size
    assert result.plain == expected


@pytest.mark.parametrize(
    ("bitrate", "expected"),
    [(0, "--"), (1_200_000, "1.2 Mb/s"), (24_300_000, "24.3 Mb/s")],
)
def test_format_bitrate_renders_megabits(bitrate, expected):
    """Verify bitrates render in megabits per second and missing values render as a dash."""
    # Given/When: Formatting a bitrate
    result = format_bitrate(bitrate)

    # Then: The text reads in Mb/s, or as a dash when absent
    assert result.plain == expected


@pytest.mark.parametrize(
    ("filename", "expected_plain", "expected_bold"),
    [
        (
            "Arrival.2016.2160p.UHD.BluRay.HEVC.DTS-HD.MA.7.1-SWTYBLZ.mkv",
            "Arrival 2016 2160p.UHD.BluRay.HEVC.DTS-HD.MA.7.1-SWTYBLZ.mkv",
            "Arrival 2016",
        ),
        (
            "The.Bear.S03E07.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv",
            "The Bear S03E07 1080p.WEB-DL.DDP5.1.H.264-NTb.mkv",
            "The Bear S03E07",
        ),
        ("home video clip.mp4", "home video clip.mp4", "home video clip"),
        # Plex/Jellyfin/Sonarr/Radarr rename conventions: no dot separates the title
        # from the trailing year/episode punctuation, so nothing should be split off.
        ("Arrival (2016).mkv", "Arrival (2016).mkv", "Arrival (2016)"),
        (
            "The Bear - S03E07 - Napkins.mkv",
            "The Bear - S03E07 - Napkins.mkv",
            "The Bear - S03E07 - Napkins",
        ),
        ("Movie.2016.mkv", "Movie 2016.mkv", "Movie 2016"),
        ("Show.S03E07.mkv", "Show S03E07.mkv", "Show S03E07"),
        (
            "2001 A Space Odyssey (1968).mkv",
            "2001 A Space Odyssey (1968).mkv",
            "2001 A Space Odyssey (1968)",
        ),
        # Leading dots (AppleDouble sidecar files, and genuinely hidden dotfiles) are not
        # a scene-release separator; they must round-trip to disk exactly, never stripped.
        ("._Sidecar.mkv", "._Sidecar.mkv", "._Sidecar"),
        (".private.mkv", ".private.mkv", ".private"),
        ("..hidden.mkv", "..hidden.mkv", "..hidden"),
        # A boundary dot that is itself the last character before the suffix leaves an
        # empty tail; no bridging space should be manufactured from nothing.
        ("Arrival.2016..mkv", "Arrival 2016.mkv", "Arrival 2016"),
        ("Show.S03E07..mkv", "Show S03E07.mkv", "Show S03E07"),
    ],
)
def test_format_name_splits_title_from_release_metadata(filename, expected_plain, expected_bold):
    """Verify the title renders bold and the release metadata tail renders dim."""
    # Given/When: Formatting a filename
    result = format_name(filename)

    # Then: The full name survives and only the title carries bold
    assert result.plain == expected_plain
    bold = "".join(
        result.plain[span.start : span.end] for span in result.spans if "bold" in str(span.style)
    )
    assert bold == expected_bold


def test_format_name_preserves_dots_in_metadata():
    """Verify dots inside technical tokens survive so DTS-HD.MA.7.1 stays readable."""
    # Given/When: Formatting a name whose tail holds dotted technical tokens
    result = format_name("Blade.Runner.2049.2017.1080p.x264.DTS-HD.MA.5.1-FGT.mkv")

    # Then: The dotted tokens are intact and the bold title runs through the LAST
    # year token, not the first (2049 would otherwise wrongly end the title)
    assert "DTS-HD.MA.5.1-FGT" in result.plain
    bold = "".join(
        result.plain[span.start : span.end] for span in result.spans if "bold" in str(span.style)
    )
    assert bold == "Blade Runner 2049 2017"


def test_format_name_never_emits_a_trailing_separator_for_an_empty_tail():
    """Verify a name with nothing after the matched token renders with no stray space."""
    # Given/When: Formatting a name whose match consumes the entire stem
    result = format_name("Show.S03E07.mkv")

    # Then: No space is inserted between the title and the suffix
    assert result.plain == "Show S03E07.mkv"
    assert "  " not in result.plain


def test_format_name_never_bridges_a_gap_that_was_not_a_dot():
    """Verify a non-dot boundary after the match is left untouched, not bridged with a space."""
    # Given/When: Formatting a name whose match is followed by a parenthesis, not a dot
    result = format_name("Arrival (2016).mkv")

    # Then: The on-disk spacing survives exactly
    assert result.plain == "Arrival (2016).mkv"


def test_format_name_prepends_a_dim_parent_segment():
    """Verify a parent directory renders as a dim segment ahead of the filename."""
    # Given/When: Formatting a filename with a parent directory
    result = format_name("S01E01 Mole Hunt.mkv", parent="tv/archer")

    # Then: The parent renders dim and the filename keeps its usual styling
    assert result.plain == "tv/archer/S01E01 Mole Hunt.mkv"
    dim = "".join(
        result.plain[span.start : span.end] for span in result.spans if "dim" in str(span.style)
    )
    assert "tv/archer/" in dim
    bold = "".join(
        result.plain[span.start : span.end] for span in result.spans if "bold" in str(span.style)
    )
    assert bold == "S01E01 Mole Hunt"


def test_format_name_omits_the_parent_segment_when_empty():
    """Verify no parent separator appears when parent is the default empty string."""
    # Given/When: Formatting a filename with no parent
    result = format_name("S01E01 Mole Hunt.mkv")

    # Then: The plain text carries no leading slash
    assert result.plain == "S01E01 Mole Hunt.mkv"


def test_format_traits_emphasizes_matches():
    """Verify matched traits render undimmed while unmatched traits render dim."""
    # Given: A trait list where only one entry matched the active filters
    traits = [VideoTrait.FHD, VideoTrait.H264, VideoTrait.COMMENTARY]
    matches = [VideoTrait.COMMENTARY]

    # When: Formatting the traits
    result = format_traits(traits, matches)

    # Then: The separator and unmatched traits are dim, the match is not
    assert result.plain == "1080p · h264 · commentary"
    dim = "".join(
        result.plain[span.start : span.end] for span in result.spans if "dim" in str(span.style)
    )
    assert "commentary" not in dim
    assert "1080p" in dim
    assert "h264" in dim


def test_format_traits_rendered_output_distinguishes_matches():
    """Verify a matched trait carries no dim escape while an unmatched one does, once rendered.

    A `format_traits` unit test on spans alone cannot catch a base style applied via the
    `Text` constructor instead of a span: that mistake is invisible until the cell is
    actually composed into a table and rendered to ANSI, which is what this test does.
    """
    # Given: A trait list where only one entry matched the active filters, placed in a
    # real table cell rather than inspected as a standalone Text object
    traits = [VideoTrait.FHD, VideoTrait.H264, VideoTrait.COMMENTARY]
    matches = [VideoTrait.COMMENTARY]
    table = Table()
    table.add_column("Traits")
    table.add_row(format_traits(traits, matches))

    # When: Rendering the composed table to ANSI-capable output
    console = Console(force_terminal=True, width=80, color_system="standard")
    with console.capture() as capture:
        console.print(table)
    rendered = capture.get()

    # Then: The unmatched traits are wrapped in a dim escape, the matched one is not
    assert "\x1b[2mh264\x1b[0m" in rendered
    assert "\x1b[2m1080p\x1b[0m" in rendered
    assert "\x1b[2mcommentary" not in rendered
    assert "commentary" in rendered


@pytest.mark.parametrize(
    ("active", "descending", "expected"),
    [(False, True, "Size"), (True, True, "Size ↓"), (True, False, "Size ↑")],
)
def test_sort_header_marks_the_active_column(active, descending, expected):
    """Verify only the active sort column carries a direction arrow."""
    # Given/When: Building a column header
    result = sort_header("Size", active=active, descending=descending)

    # Then: The arrow appears only when the column is the active sort key
    assert result.plain == expected
