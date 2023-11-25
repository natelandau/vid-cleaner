# type: ignore
"""Test helpers."""

from pathlib import Path

import pytest
import typer

from video_transcode.utils import console, existing_file_path, tmp_to_output


def test_existing_file_path_1(tmp_path):
    """Test existing_file_path helper."""
    # GIVEN a file that exists
    file = tmp_path / "test.txt"
    file.touch()

    # WHEN existing_file_path is called
    # THEN it should return the file path
    assert existing_file_path(file) == file


def test_existing_file_path_2(tmp_path):
    """Test existing_file_path helper."""
    # GIVEN a file that does not exist
    file = tmp_path / "test2.txt"

    # WHEN existing_file_path is called
    # THEN raise typer.BadParameter
    with pytest.raises(typer.BadParameter):
        existing_file_path(file)


def test_existing_file_path_3(tmp_path):
    """Test existing_file_path helper."""
    # GIVEN a directory that exists
    directory = tmp_path / "test_dir"
    directory.mkdir()

    # WHEN existing_file_path is called
    # THEN raise typer.BadParameter
    with pytest.raises(typer.BadParameter):
        existing_file_path(directory)


def test_tmp_to_output_1(tmp_path):
    """Test tmp_to_output helper."""
    # GIVEN a temporary file
    tmp_file = tmp_path / "test.txt"
    tmp_file.touch()

    # WHEN tmp_to_output is called
    result = tmp_to_output(tmp_file, "test_filename")

    # THEN it should return the file path
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename.txt"
    assert result.exists()
    assert result.is_file()

    # WHEN tmp_to_output is called again
    result = tmp_to_output(tmp_file, "test_filename")

    # THEN it should return the file path with a suffix
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename_1.txt"
    assert result.exists()
    assert result.is_file()

    # WHEN overwrite is set to True
    result = tmp_to_output(tmp_file, "test_filename", overwrite=True)

    # THEN it should return the file path
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename.txt"
    assert result.exists()
    assert result.is_file()


def test_tmp_to_output_2(tmp_path):
    """Test tmp_to_output helper."""
    # GIVEN a temporary file
    tmp_file = tmp_path / "test.txt"
    tmp_file.touch()

    # WHEN tmp_to_output is called with a new_file argument
    result = tmp_to_output(tmp_file, "test_filename", new_file=tmp_path / "test" / "new_file.txt")

    # THEN it should return the file path
    assert isinstance(result, Path)
    assert result == tmp_path / "test" / "new_file.txt"
    assert result.exists()
    assert result.is_file()
