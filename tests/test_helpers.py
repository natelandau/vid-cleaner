# type: ignore
"""Test helpers."""

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer

from vid_cleaner.utils import console, copy_with_callback, errors, existing_file_path, tmp_to_output
from vid_cleaner.utils.helpers import _copyfileobj


def test_copy_with_callback_success(tmp_path):
    """Test copy_with_callback helper."""
    # GIVEN existing source file and a destination path
    src = tmp_path / "source.txt"
    dest = tmp_path / "destination.txt"
    src.write_text("Sample data")
    callback = MagicMock()

    # WHEN copy_with_callback is called
    result = copy_with_callback(src, dest, callback)

    # THEN the destination file should have the same content
    assert dest.read_text() == "Sample data"
    assert result == dest
    # AND the callback should have been called at least once
    assert callback.called


def test_copy_with_callback_file_not_found():
    """Test copy_with_callback helper."""
    # GIVEN non-existent source file
    src = Path("/nonexistent/source.txt")
    dest = Path("/some/destination.txt")

    # WHEN copy_with_callback is called
    # THEN it should raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        copy_with_callback(src, dest)


def test_copy_with_callback_same_file(tmp_path):
    """Test copy_with_callback helper."""
    # GIVEN same source and destination file
    src = tmp_path / "source.txt"
    src.touch()  # Create an empty file

    # WHEN copy_with_callback is called with the same file as source and dest
    # THEN it should raise SameFileError
    with pytest.raises(errors.SameFileError):
        copy_with_callback(src, src)


def test_copy_with_callback_invalid_callback(tmp_path):
    """Test copy_with_callback helper."""
    # GIVEN valid source and destination, but invalid callback
    src = tmp_path / "source.txt"
    dest = tmp_path / "destination.txt"
    src.touch()
    invalid_callback = "not a callable"

    # WHEN copy_with_callback is called with an invalid callback
    # THEN it should raise ValueError
    with pytest.raises(ValueError, match="callback must be callable"):
        copy_with_callback(src, dest, invalid_callback)


def test_copyfileobj():
    """Test _copyfileobj helper."""
    src_data = b"Hello World" * 100  # Sample data
    src = io.BytesIO(src_data)  # Source buffer
    dest = io.BytesIO()  # Destination buffer
    callback = MagicMock()  # Mock callback
    length = 20  # Length of data to copy at once

    # Call _copyfileobj
    _copyfileobj(src, dest, callback, length)

    # Check if all data was copied correctly
    assert dest.getvalue() == src_data

    # Check if callback was called the correct number of times
    expected_calls = len(src_data) // length
    assert callback.call_count == expected_calls


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
