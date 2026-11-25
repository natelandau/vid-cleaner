"""Test the discovery controller."""

import os

import pytest

from vid_cleaner.constants import SortOrder, VideoTrait
from vid_cleaner.controllers.discovery import (
    DiscoveryReport,
    _walk_video_paths,
    coerce_bitrate,
    discover_video_files,
)
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import ProbeCache

# A cache with no directory, so every test probes its files
NO_CACHE = ProbeCache(None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("5000000", 5_000_000, id="numeric_string"),
        pytest.param(5_000_000, 5_000_000, id="integer"),
        pytest.param(None, 0, id="missing"),
        pytest.param("", 0, id="empty_string"),
        pytest.param("abc", 0, id="unparsable"),
    ],
)
def test_coerce_bitrate(raw, expected):
    """Verify ffprobe's string, integer, missing, and unparsable bitrates all yield an int."""
    assert coerce_bitrate(raw) == expected


def test_discover_returns_empty_report_for_empty_directory(tmp_path):
    """Verify a directory with no video files reports zero total without raising."""
    # Given: An empty directory
    directory = tmp_path / "empty"
    directory.mkdir()

    # When: Discovering video files
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: The report is empty rather than an exception or an exit
    assert isinstance(report, DiscoveryReport)
    assert report.total == 0
    assert report.results == []
    assert report.skipped == []


def test_discover_reports_total_and_filters(video_library):
    """Verify total counts every candidate while results hold only trait matches."""
    # Given: Two probeable files, both h264 per the reference fixture
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 2_000_000)])

    # When: Filtering for a trait both files have
    report = discover_video_files(directory, probe_cache=NO_CACHE, filters={VideoTrait.H264})

    # Then: Both files are counted and both match
    assert report.total == 2
    assert len(report.results) == 2
    assert report.filtered is True


def test_discover_multiple_filters_require_every_trait(video_library):
    """Verify a file missing any one of the active filters is dropped."""
    # Given: Two probeable files, both h264 and 1080p but neither needing a stream reorder
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 2_000_000)])

    # When: Filtering for a trait they have alongside one they lack
    report = discover_video_files(
        directory, probe_cache=NO_CACHE, filters={VideoTrait.H264, VideoTrait.REORDER}
    )

    # Then: Neither file survives, since matching only one filter is not enough
    assert report.total == 2
    assert report.results == []


def test_discover_all_filters_present_matches(video_library):
    """Verify a file carrying every active filter survives."""
    # Given: Two probeable files, both h264 and 1080p per the reference fixture
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 2_000_000)])

    # When: Filtering for two traits both files have
    report = discover_video_files(
        directory, probe_cache=NO_CACHE, filters={VideoTrait.H264, VideoTrait.FHD}
    )

    # Then: Both survive and each reports both filters as matches
    assert len(report.results) == 2
    assert all(set(r.matches) == {VideoTrait.H264, VideoTrait.FHD} for r in report.results)


def test_discover_without_filters_keeps_everything(video_library):
    """Verify an empty filter set matches every readable file."""
    # Given: Two probeable files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 2_000_000)])

    # When: Discovering with no filters
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: Every file survives and the report says filtering was inactive
    assert len(report.results) == 2
    assert report.filtered is False


@pytest.mark.parametrize(
    ("sort", "reverse", "expected_order", "expected_descending"),
    [
        (SortOrder.ALPHA, False, ["apple.mkv", "banana.mkv", "cherry.mkv"], False),
        (SortOrder.ALPHA, True, ["cherry.mkv", "banana.mkv", "apple.mkv"], True),
        (SortOrder.SIZE, False, ["banana.mkv", "cherry.mkv", "apple.mkv"], True),
        (SortOrder.SIZE, True, ["apple.mkv", "cherry.mkv", "banana.mkv"], False),
        (SortOrder.BITRATE, False, ["cherry.mkv", "apple.mkv", "banana.mkv"], True),
        (SortOrder.BITRATE, True, ["banana.mkv", "apple.mkv", "cherry.mkv"], False),
    ],
)
def test_discover_sort_orders(video_library, sort, reverse, expected_order, expected_descending):
    """Verify each sort key and reverse produce the documented order and direction."""
    # Given: Three files whose alphabetical, size, and bitrate orders all differ
    directory = video_library(
        [
            ("apple.mkv", 100, 2_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 3_000_000),
        ]
    )

    # When: Discovering with the given sort arguments
    report = discover_video_files(directory, probe_cache=NO_CACHE, sort=sort, reverse=reverse)

    # Then: The results run in the expected order and report their real direction
    assert [r.video_file.path.name for r in report.results] == expected_order
    assert report.descending is expected_descending


def test_discover_limit_truncates_after_sorting(video_library):
    """Verify limit keeps the top N on the active sort key and counts what it cut."""
    # Given: Three files of differing size
    directory = video_library(
        [
            ("apple.mkv", 100, 1_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 1_000_000),
        ]
    )

    # When: Taking the two largest
    report = discover_video_files(directory, probe_cache=NO_CACHE, sort=SortOrder.SIZE, limit=2)

    # Then: The two largest survive and the cut file is counted
    assert [r.video_file.path.name for r in report.results] == ["banana.mkv", "cherry.mkv"]
    assert report.truncated == 1
    assert report.total == 3


def test_discover_limit_above_match_count_truncates_nothing(video_library):
    """Verify a limit larger than the match count is a no-op."""
    # Given: Two files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])

    # When: Limiting to more files than exist
    report = discover_video_files(directory, probe_cache=NO_CACHE, limit=10)

    # Then: Nothing is cut
    assert len(report.results) == 2
    assert report.truncated == 0


def test_discover_collects_unreadable_files(
    video_library,
    mocker,
):
    """Verify a file that cannot be probed is collected rather than aborting discovery."""
    # Given: One readable file and one that ffprobe rejects
    directory = video_library([("good.mkv", 100, 1_000_000)])
    bad = directory / "bad.mkv"
    bad.touch()
    good_box = mocker.MagicMock()

    def probe(path, **_kwargs):
        if path.name == "bad.mkv":
            raise VideoProbeError(path=path, reason="Invalid data found when processing input")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Discovering with no filters, so the readable file survives
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: The bad file is counted as skipped and the good one still appears
    assert report.total == 2
    assert len(report.skipped) == 1
    assert report.skipped[0].path.name == "bad.mkv"


def test_discover_ignores_appledouble_sidecars(video_library):
    """Verify macOS `._` sidecar files are never treated as videos."""
    # Given: A real video beside its AppleDouble sidecar, and a sidecar one level down
    directory = video_library([("movie.mkv", 100, 1_000_000), ("nested/deep.mkv", 100, 1_000_000)])
    (directory / "._movie.mkv").write_bytes(b"\x00\x05\x16\x07")
    (directory / "nested" / "._deep.mkv").write_bytes(b"\x00\x05\x16\x07")

    # When: Discovering one level deep
    report = discover_video_files(directory, probe_cache=NO_CACHE, depth=1)

    # Then: Only the real videos are found, and the sidecars are neither counted nor skipped
    assert {r.video_file.path.name for r in report.results} == {"movie.mkv", "deep.mkv"}
    assert report.total == 2
    assert report.skipped == []


@pytest.mark.parametrize(
    "make_entry",
    [
        pytest.param(os.mkfifo, id="fifo"),
        pytest.param(lambda path: path.symlink_to(path.name), id="symlink_loop"),
        pytest.param(lambda path: path.symlink_to("missing.mkv"), id="dangling_symlink"),
    ],
)
def test_discover_ignores_entries_that_are_not_regular_files(video_library, make_entry):
    """Verify a video-named entry that is not a regular file is never probed."""
    # Given: A real video beside a video-named entry that is not a regular file
    directory = video_library([("movie.mkv", 100, 1_000_000)])
    make_entry(directory / "odd.mkv")

    # When: Discovering video files
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: Only the real video is counted, and nothing is reported as skipped
    assert [r.video_file.path.name for r in report.results] == ["movie.mkv"]
    assert report.total == 1
    assert report.skipped == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any directory")
def test_discover_reports_a_directory_it_cannot_list(video_library):
    """Verify an unlistable subdirectory is reported rather than silently searched as empty."""
    # Given: A readable video and a subdirectory with no permissions
    directory = video_library([("movie.mkv", 100, 1_000_000), ("locked/hidden.mkv", 100, 0)])
    locked = directory / "locked"
    locked.chmod(0)

    # When: Discovering one level deep
    try:
        report = discover_video_files(directory, probe_cache=NO_CACHE, depth=1)
    finally:
        locked.chmod(0o755)

    # Then: The readable video is found and the locked directory is reported as skipped
    assert [r.video_file.path.name for r in report.results] == ["movie.mkv"]
    assert [error.path for error in report.skipped] == [locked]


def test_walk_visits_each_directory_once_through_a_symlink_cycle(tmp_path):
    """Verify links back to an ancestor do not multiply the walk at every level."""
    # Given: Two subdirectories that each link back to the root
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "up").symlink_to("..", target_is_directory=True)

    # When: Walking far deeper than the tree
    errors: list[OSError] = []
    walked = [
        directory for directory, _ in _walk_video_paths(tmp_path, depth=16, on_error=errors.append)
    ]

    # Then: Each real directory is walked once
    assert errors == []
    assert sorted(walked) == [tmp_path, tmp_path / "a", tmp_path / "b"]


def test_discover_matches_extensions_case_insensitively(video_library):
    """Verify a file with an uppercase video extension is discovered."""
    # Given: A video whose extension is uppercase
    directory = video_library([("MOVIE.MKV", 100, 1_000_000)])

    # When: Discovering video files
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: The file is found
    assert [r.video_file.path.name for r in report.results] == ["MOVIE.MKV"]


@pytest.mark.parametrize(
    ("link_name", "target", "is_directory"),
    [
        pytest.param("latest", "nested", True, id="symlinked directory"),
        pytest.param("nested/alias.mkv", "nested/deep.mkv", False, id="symlinked file"),
    ],
)
def test_discover_lists_a_file_reached_through_a_symlink_once(
    video_library, link_name, target, is_directory
):
    """Verify a file reachable through a symlink is listed, and so cleaned, only once."""
    # Given: A nested video and a symlink that reaches it a second way
    directory = video_library([("nested/deep.mkv", 100, 1_000_000)])
    (directory / link_name).symlink_to(directory / target, target_is_directory=is_directory)

    # When: Discovering one level deep, which visits both paths
    report = discover_video_files(directory, probe_cache=NO_CACHE, depth=1)

    # Then: The file appears once
    assert [r.video_file.path.name for r in report.results] == ["deep.mkv"]
    assert report.total == 1


def test_discover_depth_zero_ignores_subdirectories(video_library):
    """Verify the default depth does not descend into subdirectories."""
    # Given: One top-level file and one nested a directory below
    directory = video_library([("top.mkv", 100, 1_000_000), ("nested/deep.mkv", 100, 1_000_000)])

    # When: Discovering at the default depth
    report = discover_video_files(directory, probe_cache=NO_CACHE)

    # Then: Only the top-level file is found
    assert [r.video_file.path.name for r in report.results] == ["top.mkv"]


def test_discover_depth_one_descends(video_library):
    """Verify depth 1 finds files one directory below the root."""
    # Given: One top-level file and one nested a directory below
    directory = video_library([("top.mkv", 100, 1_000_000), ("nested/deep.mkv", 100, 1_000_000)])

    # When: Discovering one level deep
    report = discover_video_files(directory, probe_cache=NO_CACHE, depth=1)

    # Then: Both files are found
    assert {r.video_file.path.name for r in report.results} == {"top.mkv", "deep.mkv"}


def test_discover_ties_break_alphabetically(video_library, mocker):
    """Verify equal values on the active key fall back to path order, not scan order."""
    # Given: Three identically sized files
    directory = video_library(
        [("alpha.mkv", 100, 0), ("bravo.mkv", 100, 0), ("charlie.mkv", 100, 0)]
    )
    # Directory listing order is arbitrary; scramble it to prove the tiebreaker is real.
    scrambled = [directory / "bravo.mkv", directory / "charlie.mkv", directory / "alpha.mkv"]
    mocker.patch(
        "vid_cleaner.controllers.discovery._walk_video_paths",
        return_value=[(directory, scrambled)],
    )

    # When: Sorting by a key that cannot break the tie
    report = discover_video_files(directory, probe_cache=NO_CACHE, sort=SortOrder.SIZE)

    # Then: Ties resolve alphabetically by path
    assert [r.video_file.path.name for r in report.results] == [
        "alpha.mkv",
        "bravo.mkv",
        "charlie.mkv",
    ]
