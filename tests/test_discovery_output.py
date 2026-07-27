# type: ignore
"""Test the shared discovery presenter."""

import cappa
import pytest

from vid_cleaner.cli.discovery_output import present_discovery
from vid_cleaner.constants import SortOrder, VideoTrait
from vid_cleaner.controllers.discovery import DiscoveryReport
from vid_cleaner.exceptions import VideoProbeError


def make_report(**overrides) -> DiscoveryReport:
    """Build a DiscoveryReport with test-friendly defaults.

    Returns:
        DiscoveryReport: A report with every field defaulted unless overridden.
    """
    defaults = {
        "results": [],
        "total": 0,
        "skipped": [],
        "truncated": 0,
        "sort": SortOrder.ALPHA,
        "descending": False,
        "filtered": False,
    }
    return DiscoveryReport(**{**defaults, **overrides})


def test_present_exits_zero_when_nothing_found(capsys, tmp_path):
    """Verify an empty directory warns and exits 0, since finding nothing is not an error."""
    # Given: A report from a directory holding no video files
    report = make_report(total=0)

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path, filters=set(), recursive=False)

    # Then: The command warns and exits successfully
    assert exc_info.value.code == 0
    assert "No video files found" in capsys.readouterr().err


def test_present_exits_one_when_nothing_matched(capsys, tmp_path):
    """Verify files found but none matching the filters is an error naming the filters."""
    # Given: A report where candidates existed but none matched
    report = make_report(total=3, filtered=True)

    # When: Presenting it with an active filter
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path, filters={VideoTrait.REORDER}, recursive=False)

    # Then: The command errors and names the unmatched filter
    assert exc_info.value.code == 1
    assert "No video files found matching 'reorder'" in capsys.readouterr().err


def test_present_empty_match_reports_skipped_count(capsys, tmp_path):
    """Verify the no-match error folds in the skipped count, since no caption will render."""
    # Given: A report where every candidate was unreadable
    report = make_report(
        total=1,
        filtered=True,
        skipped=[VideoProbeError(path=tmp_path / "bad.mkv", reason="Invalid data")],
    )

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path, filters={VideoTrait.REORDER}, recursive=False)

    # Then: The error reports the skipped file alongside the mismatch
    error = capsys.readouterr().err
    assert exc_info.value.code == 1
    assert "1 file(s) skipped as unreadable" in error


def test_present_unfiltered_empty_match_says_unreadable(capsys, tmp_path):
    """Verify an unfiltered run that matched nothing blames readability, not the filters."""
    # Given: A report with candidates, no filters, and no results
    report = make_report(total=2, filtered=False)

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path, filters=set(), recursive=False)

    # Then: The error explains that nothing could be read
    assert exc_info.value.code == 1
    assert "No video files could be read" in capsys.readouterr().err
