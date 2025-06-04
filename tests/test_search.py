# type: ignore
"""Test the search command."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
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
            "save_each_step": False,
        }
    )


def test_search_no_video_files(tmp_path, clean_stdout, mocker, debug):
    """Test that the search command returns no results when there are no video files."""
    # Given: A directory with no video files
    directory = Path(tmp_path) / "no_videos"
    directory.mkdir(parents=True, exist_ok=True)

    # When: Running the search command
    args = ["search", str(directory)]

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # Then: The command should return no results
    # debug(output, "output")
    assert exc_info.value.code == 0
    assert "No video files found" in output


def test_search_with_results(
    tmp_path,
    clean_stdout,
    mocker,
    mock_video_path,
    # mock_video_file,
    debug,
    mock_ffprobe_box,
    mock_ffprobe,
):
    """Test that the search command returns results when there are video files."""
    # When: Running the search command
    args = ["search", str(mock_video_path.parent), "--filters", "h264,reorder"]

    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # Then: The command should return results
    # debug(output, "output")

    assert exc_info.value.code == 0
    assert "Found 1 video files in 1/1 directories" in output
    assert "1 │ test_video.mp4 │ h264" in output


def test_search_with_no_results(
    tmp_path,
    clean_stdout,
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

    output = clean_stdout()
    # Then: The command should return results
    # debug(output, "output")

    assert exc_info.value.code == 1
    assert "Found 1 video files in 1/1 directories" in output
    assert "No video files found matching 'reorder'" in output
    assert "Matching filters" not in output
