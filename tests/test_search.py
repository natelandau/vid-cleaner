# type: ignore
"""Test the search command."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.fixture(autouse=True)
def set_default_settings(tmp_path, mocker, mock_ffprobe_box):
    """Set default settings for tests."""
    cache_dir = Path(tmp_path) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings.update(
        {
            "cache_dir": cache_dir,
            "langs_to_keep": ["en"],
            "downmix_stereo": False,
            "keep_local_subtitles": False,
            "keep_commentary": False,
            "drop_local_subs": False,
            "keep_all_subtitles": False,
            "drop_original_audio": False,
        }
    )


def test_search_no_video_files(tmp_path, capsys, mocker, debug):
    """Test that the search command returns no results when there are no video files."""
    # Given: A directory with no video files
    directory = Path(tmp_path) / "no_videos"
    directory.mkdir(parents=True, exist_ok=True)

    # When: Running the search command
    args = ["search", str(directory)]

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().err
    # Then: The command should return no results
    debug(output, "output")
    assert exc_info.value.code == 0
    assert "No video files found" in output


def test_search_with_results(
    tmp_path,
    capsys,
    mocker,
    mock_video_path,
    # mock_video_file,
    debug,
    mock_ffprobe_box,
    mock_ffprobe,
):
    """Test that the search command returns results when there are video files."""
    # When: Running the search command
    args = ["search", str(mock_video_path.parent), "--filters", "h264,1080p"]

    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out
    # Then: The command should return results
    # debug(output, "output")

    assert exc_info.value.code == 0
    assert "Found 1 video files in 1/1 directories" in output
    assert "test_video.mp4" in output
    assert "h264" in output


def test_search_with_no_results(
    tmp_path,
    capsys,
    mocker,
    mock_video_path,
    # mock_video_file,
    debug,
    mock_ffprobe_box,
    mock_ffprobe,
):
    """Test that the search command returns results when there are video files."""
    # When: Running the search command
    args = ["search", str(mock_video_path.parent), "--filters", "reorder"]

    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()
    # Then: The command should return results
    # debug(output, "output")

    assert exc_info.value.code == 1
    assert "Found 1 video files in 1/1 directories" in output
    assert "No video files found matching 'reorder'" in error
    assert "Bitrate" not in output


def test_search_filters_combine_with_and(capsys, mocker, mock_video_path, mock_ffprobe_box):
    """Verify a file matching only some of the filters is not returned."""
    # Given: A file that is h264 but needs no stream reorder
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    # When: Searching for a trait it has together with one it lacks
    args = ["search", str(mock_video_path.parent), "--filters", "h264,reorder"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    _, error = capsys.readouterr()

    # Then: Matching one filter is not enough and the error names both
    assert exc_info.value.code == 1
    assert "No video files found matching 'h264' and 'reorder'" in error


def test_search_no_results_reports_skipped_count(capsys, mock_video_path, mocker, mock_ffprobe_box):
    """Verify the no-results error still surfaces how many files were skipped as unreadable."""
    # Given: One file that fails to probe and one that probes but matches no active filter
    unreadable = mock_video_path.parent / "not_really_a_video.mkv"
    unreadable.touch()
    good_box = mock_ffprobe_box("reference.json")

    def probe(path):
        if path.name == unreadable.name:
            raise VideoProbeError(path=path, reason="Invalid data found when processing input")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Filtering for a trait no readable file has, so results is empty and the
    # success-path table caption (the usual home for the skipped count) never renders
    args = ["search", str(mock_video_path.parent), "--filters", "reorder"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    _, error = capsys.readouterr()

    # Then: The error reports both the mismatch and the skipped file
    assert exc_info.value.code == 1
    assert "No video files found matching 'reorder'" in error
    assert "1" in error
    assert "skipped" in error


@pytest.mark.parametrize(
    ("verbosity", "lists_skipped_files"),
    [(None, False), ("-v", True)],
)
def test_search_skips_unreadable_files(
    capsys,
    mocker,
    mock_video_path,
    debug,
    mock_ffprobe_box,
    mock_ffprobe,
    verbosity,
    lists_skipped_files,
):
    """Verify search reports and skips files it cannot probe while still listing readable ones."""
    # Given: A directory holding one readable video and one file that ffprobe rejects
    unreadable = mock_video_path.parent / "not_really_a_video.mkv"
    unreadable.touch()
    good_box = mock_ffprobe_box("reference.json")

    def probe(path):
        if path.name == unreadable.name:
            raise VideoProbeError(path=path, reason="Invalid data found when processing input")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    args = ["search", str(mock_video_path.parent), "--filters", "h264"]
    if verbosity:
        args.insert(0, verbosity)

    # When: Running the search command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()
    debug(output + error, "output")

    # Then: The readable file is still reported and the unreadable one is only counted
    assert exc_info.value.code == 0
    assert "test_video.mp4" in output
    assert "h264" in output
    assert "1 skipped" in output
    assert ("Unreadable files" in output + error) is lists_skipped_files


def test_search_skips_files_that_vanish_before_stat(
    capsys, mocker, mock_video_path, mock_ffprobe_box
):
    """Verify a file that disappears between probing and stat() is skipped, not fatal."""
    # Given: A second file that probes fine but vanishes before its size can be read,
    # e.g. deleted mid-scan by another process
    vanished = mock_video_path.parent / "vanished.mkv"
    vanished.touch()
    good_box = mock_ffprobe_box("reference.json")

    # Discovery finishes before any file is probed, so deleting the file here leaves it
    # present for the scan that found it and genuinely gone by the time its size is read.
    # `missing_ok` because each probed file is re-probed on every stream-property access.
    def probe(path: Path) -> object:
        if path.name == vanished.name:
            vanished.unlink(missing_ok=True)
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Running the search command
    args = ["search", str(mock_video_path.parent), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The scan completes, reporting the readable file and counting the vanished
    # one as skipped rather than aborting the whole search
    assert exc_info.value.code == 0
    assert "test_video.mp4" in output
    assert "1 skipped" in output


@pytest.mark.parametrize(
    ("sort_args", "expected_order"),
    [
        ([], ["apple", "banana", "cherry"]),
        (["--sort", "alpha"], ["apple", "banana", "cherry"]),
        (["--sort", "alpha", "--reverse"], ["cherry", "banana", "apple"]),
        (["--sort", "size"], ["banana", "cherry", "apple"]),
        (["--sort", "size", "--reverse"], ["apple", "cherry", "banana"]),
        (["--sort", "bitrate"], ["cherry", "apple", "banana"]),
        (["--sort", "bitrate", "--reverse"], ["banana", "apple", "cherry"]),
    ],
)
def test_search_sort_orders_results(capsys, video_library, sort_args, expected_order):
    """Verify each sort key and --reverse order the results as documented."""
    # Given: Three files whose alphabetical, size, and bitrate orders all differ
    directory = video_library(
        [
            ("apple.mkv", 100, 2_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 3_000_000),
        ]
    )

    # When: Running the search command with the given sort arguments
    args = ["search", str(directory), "--filters", "h264", *sort_args]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The rows appear in the expected order
    assert exc_info.value.code == 0
    positions = [output.index(name) for name in expected_order]
    assert positions == sorted(positions)


def test_search_sort_alpha_uses_full_path(capsys, video_library):
    """Verify alphabetical sorting groups results by directory, not by bare filename."""
    # Given: Files whose directory order and filename order disagree
    directory = video_library([("zulu.mkv", 100, 1_000_000), ("aaa/mike.mkv", 100, 1_000_000)])

    # When: Running a recursive search with the default sort
    args = ["search", str(directory), "--depth", "1", "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The nested `aaa/mike.mkv` sorts before the top-level `zulu.mkv`
    assert exc_info.value.code == 0
    assert output.index("mike") < output.index("zulu")


def test_search_table_shows_relative_directory_when_recursive(capsys, video_library):
    """Verify a recursive search renders each result's directory as a dim path segment."""
    # Given: A file nested one directory below the search root
    directory = video_library([("zulu.mkv", 100, 1_000_000), ("aaa/mike.mkv", 100, 1_000_000)])

    # When: Running a recursive search
    args = ["search", str(directory), "--depth", "1", "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The nested result's row shows its directory ahead of the filename
    assert exc_info.value.code == 0
    assert "aaa/mike.mkv" in output


def test_search_table_omits_directory_when_not_recursive(capsys, video_library):
    """Verify a depth-0 search renders the bare filename with no directory prefix."""
    # Given: A file at the top level of the search directory
    directory = video_library([("mike.mkv", 100, 1_000_000)])

    # When: Running a non-recursive search (the default depth)
    args = ["search", str(directory), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Only the bare filename appears, with no directory segment
    assert exc_info.value.code == 0
    assert "mike.mkv" in output
    assert str(directory) not in output


def test_search_sort_bitrate_handles_missing_bitrate(capsys, video_library):
    """Verify a file whose probe omits bit_rate sorts last instead of raising."""
    # Given: One file with a bitrate and one without
    directory = video_library([("apple.mkv", 100, 0), ("banana.mkv", 100, 5_000_000)])

    # When: Sorting by bitrate
    args = ["search", str(directory), "--filters", "h264", "--sort", "bitrate"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The file with a bitrate sorts ahead of the one without
    assert exc_info.value.code == 0
    assert output.index("banana") < output.index("apple")


def test_search_sort_ties_are_deterministic(capsys, video_library, mocker):
    """Verify equal-size files render in a stable order regardless of scan order."""
    # Given: Three files with an identical size, so `--sort=size` cannot break the tie
    directory = video_library(
        [("alpha.mkv", 100, 0), ("bravo.mkv", 100, 0), ("charlie.mkv", 100, 0)]
    )
    # find_files' scan order is arbitrary; scramble it here to prove the table's row
    # order does not simply mirror whatever order the filesystem happened to return.
    scrambled = [directory / "bravo.mkv", directory / "charlie.mkv", directory / "alpha.mkv"]
    mocker.patch("vid_cleaner.controllers.discovery.find_files", return_value=scrambled)

    # When: Sorting by size
    args = ["search", str(directory), "--filters", "h264", "--sort", "size"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Ties fall back to alphabetical-by-path order, not scan order
    assert exc_info.value.code == 0
    assert output.index("alpha") < output.index("bravo") < output.index("charlie")


def test_search_rejects_unknown_sort_key(capsys):
    """Verify an unsupported --sort value is refused by the CLI."""
    # Given: An invalid sort key
    args = ["search", ".", "--sort", "duration"]

    # When: Running the search command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command exits with an error
    assert exc_info.value.code != 0


def test_search_table_shows_size_and_bitrate(capsys, video_library):
    """Verify the results table reports each file's size and bitrate."""
    # Given: A single file with a known size and bitrate
    directory = video_library([("apple.mkv", 1500, 2_000_000)])

    # When: Running the search command
    args = ["search", str(directory), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Both columns and both values are present
    assert exc_info.value.code == 0
    assert "Size" in output
    assert "Bitrate" in output
    assert "1.5 kB" in output
    assert "2.0 Mb/s" in output


def test_search_table_marks_the_active_sort_column(capsys, video_library):
    """Verify the sorted column header carries a direction arrow that --reverse flips."""
    # Given: A directory with one matching file
    directory = video_library([("apple.mkv", 100, 2_000_000)])

    # When: Sorting by size
    args = ["search", str(directory), "--filters", "h264", "--sort", "size"]
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])
    forward = capsys.readouterr().out

    # When: Sorting by size in reverse
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=[*args, "--reverse"], deps=[config_subcommand])
    reversed_output = capsys.readouterr().out

    # Then: The arrow marks the size column and flips with --reverse
    assert "Size ↓" in forward
    assert "Size ↑" in reversed_output


def test_search_table_arrow_reports_the_direction_rows_run(capsys, video_library):
    """Verify the arrow follows the rendered order, not the --reverse flag."""
    # Given: Two files whose alphabetical order is visible in the table
    directory = video_library([("apple.mkv", 100, 2_000_000), ("banana.mkv", 100, 1_000_000)])

    # When: Sorting alphabetically, which ascends by default
    args = ["search", str(directory), "--filters", "h264"]
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])
    forward = capsys.readouterr().out

    # When: Reversing that order
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=[*args, "--reverse"], deps=[config_subcommand])
    reversed_output = capsys.readouterr().out

    # Then: A→Z rows carry the up arrow and Z→A rows the down arrow, matching the
    # meaning the size and bitrate columns give the same symbols
    assert "File ↑" in forward
    assert forward.index("apple") < forward.index("banana")
    assert "File ↓" in reversed_output
    assert reversed_output.index("banana") < reversed_output.index("apple")


def test_search_table_caption_reports_counts(capsys, video_library):
    """Verify the caption reports how many files matched out of those scanned."""
    # Given: Two files where only one matches the active filter
    directory = video_library([("apple.mkv", 100, 2_000_000), ("banana.mkv", 100, 1_000_000)])

    # When: Running a search whose filter matches both
    args = ["search", str(directory), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The caption states the matched and scanned counts
    assert exc_info.value.code == 0
    assert "2 of 2 files matched" in output


def test_search_limit_keeps_top_results(capsys, video_library):
    """Verify --limit keeps only the top N on the active sort key."""
    # Given: Three files of differing size
    directory = video_library(
        [
            ("apple.mkv", 100, 1_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 1_000_000),
        ]
    )

    # When: Asking for the two largest
    args = ["search", str(directory), "--filters", "h264", "--sort", "size", "--limit", "2"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Only the two largest render
    assert exc_info.value.code == 0
    assert "banana" in output
    assert "cherry" in output
    assert "apple" not in output


def test_search_limit_caption_reports_truncation(capsys, video_library):
    """Verify a limited view says so, so it never reads as a complete result set."""
    # Given: Three matching files
    directory = video_library(
        [
            ("apple.mkv", 100, 1_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 1_000_000),
        ]
    )

    # When: Limiting to two
    args = ["search", str(directory), "--filters", "h264", "--sort", "size", "--limit", "2"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The caption distinguishes shown from matched
    assert exc_info.value.code == 0
    assert "showing 2" in output
    assert "3 of 3 files matched" in output


def test_search_without_limit_caption_omits_truncation(capsys, video_library):
    """Verify an unlimited view's caption is unchanged, with no 'showing' prefix."""
    # Given: Two matching files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])

    # When: Searching with no limit
    args = ["search", str(directory), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The caption reads exactly as it did before --limit existed
    assert exc_info.value.code == 0
    assert "2 of 2 files matched" in output
    assert "showing" not in output


def test_search_table_caption_reports_skipped_files(
    capsys, mock_video_path, mocker, mock_ffprobe_box
):
    """Verify the caption counts files that could not be read as video."""
    # Given: One readable video and one file ffprobe rejects
    unreadable = mock_video_path.parent / "not_really_a_video.mkv"
    unreadable.touch()
    good_box = mock_ffprobe_box("reference.json")

    def probe(path):
        if path.name == unreadable.name:
            raise VideoProbeError(path=path, reason="Invalid data found when processing input")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Running the search command
    args = ["search", str(mock_video_path.parent), "--filters", "h264"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The caption reports the skipped file
    assert exc_info.value.code == 0
    assert "1 skipped" in output
