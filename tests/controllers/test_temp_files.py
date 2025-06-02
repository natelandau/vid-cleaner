"""Test TempFile controller functionality."""

from pathlib import Path

from vid_cleaner import settings

from vid_cleaner.controllers import TempFile  # isort: skip


def test_temp_file_latest_temp_path(mock_video_path: Path, tmp_path: Path) -> None:
    """Verify original path is returned when no temp files exist."""
    # Given: A TempFile instance with cache directory set
    settings.update({"cache_dir": Path(tmp_path)})
    temp_file = TempFile(mock_video_path)

    # When: Getting latest temp path with no temp files
    result = temp_file.latest_temp_path()

    # Then: Original path is returned
    assert result == mock_video_path


def test_temp_file_new_tmp_path(mock_video_path: Path, tmp_path: Path) -> None:
    """Verify new temp file paths are generated with optional suffix and step name."""
    # Given: A TempFile instance with cache directory set
    settings.update({"cache_dir": Path(tmp_path)})
    temp_file = TempFile(mock_video_path)

    # When: Generating new temp paths with different options
    default_path = temp_file.new_tmp_path()
    custom_path = temp_file.new_tmp_path(suffix=".mkv", step_name="somename")

    # Then: Paths are generated with correct naming
    assert default_path.name == "1_no_step.mp4"
    assert custom_path.name == "1_somename.mkv"
    assert temp_file.created_tmp_files == []


def test_temp_file_created_temp_file(mock_video_path: Path, tmp_path: Path) -> None:
    """Verify newly created temp files are tracked correctly."""
    # Given: A TempFile instance with cache directory set
    settings.update({"cache_dir": Path(tmp_path)})
    temp_file = TempFile(mock_video_path)

    # When: Creating and tracking a new temp file
    output_path = temp_file.new_tmp_path(suffix=".mkv", step_name="somename")
    temp_file.created_temp_file(output_path)

    # Then: File is tracked in created_tmp_files
    assert temp_file.created_tmp_files == [output_path]


def test_temp_file_clean_old_tmp_files(mock_video_path: Path, tmp_path: Path) -> None:
    """Verify all but latest temp file are removed."""
    # Given: A TempFile instance with multiple temp files
    settings.update({"cache_dir": Path(tmp_path)})
    temp_file = TempFile(mock_video_path)

    # When: Creating multiple temp files
    output_path_1 = temp_file.new_tmp_path()
    output_path_1.touch()
    temp_file.created_temp_file(output_path_1)

    output_path_2 = temp_file.new_tmp_path()
    output_path_2.touch()
    temp_file.created_temp_file(output_path_2)

    output_path_3 = temp_file.new_tmp_path()
    output_path_3.touch()
    temp_file.created_temp_file(output_path_3)

    # When: Cleaning old temp files
    temp_file.clean_old_tmp_files()

    # Then: Only latest file remains
    assert temp_file.created_tmp_files == [output_path_1, output_path_2, output_path_3]
    assert not output_path_1.exists()
    assert not output_path_2.exists()
    assert output_path_3.exists()


def test_temp_file_clean_up(mock_video_path: Path, tmp_path: Path) -> None:
    """Verify all temp files and temp directory are removed."""
    # Given: A TempFile instance with multiple temp files
    settings.update({"cache_dir": Path(tmp_path)})
    temp_file = TempFile(mock_video_path)

    # When: Creating multiple temp files
    output_path_1 = temp_file.new_tmp_path()
    output_path_1.touch()
    assert output_path_1.name.startswith("1_")
    temp_file.created_temp_file(output_path_1)

    output_path_2 = temp_file.new_tmp_path()
    output_path_2.touch()
    assert output_path_2.name.startswith("2_")
    temp_file.created_temp_file(output_path_2)

    output_path_3 = temp_file.new_tmp_path()
    output_path_3.touch()
    assert output_path_3.name.startswith("3_")
    temp_file.created_temp_file(output_path_3)

    # When: Cleaning up all temp files
    temp_file.clean_up()

    # Then: All temp files and directory are removed
    assert not output_path_1.exists()
    assert not output_path_2.exists()
    assert not output_path_3.exists()
    assert not output_path_3.parent.exists()
