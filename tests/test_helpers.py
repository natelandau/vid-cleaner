# type: ignore
"""Test helpers."""

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer

from vid_cleaner.utils import copy_with_callback, errors, existing_file_path, tmp_to_output
from vid_cleaner.utils.helpers import _copyfileobj  # noqa: PLC2701


def test_copy_with_callback_success(tmp_path):
    """Verify copy_with_callback successfully copies file and invokes callback."""
    # GIVEN existing source file and destination path
    src = tmp_path / "source.txt"
    dest = tmp_path / "destination.txt"
    src.write_text("Sample data")
    callback = MagicMock()

    # WHEN copy_with_callback is called
    result = copy_with_callback(src, dest, callback)

    # THEN verify destination file has same content
    assert dest.read_text() == "Sample data"
    assert result == dest
    # AND verify callback was invoked
    assert callback.called


def test_copy_with_callback_file_not_found():
    """Verify copy_with_callback raises FileNotFoundError for missing source."""
    # GIVEN non-existent source file path
    src = Path("/nonexistent/source.txt")
    dest = Path("/some/destination.txt")

    # WHEN copy_with_callback is called with missing source
    # THEN verify FileNotFoundError is raised
    with pytest.raises(FileNotFoundError):
        copy_with_callback(src, dest)


def test_copy_with_callback_same_file(tmp_path):
    """Verify copy_with_callback raises SameFileError when source equals destination."""
    # GIVEN source file path
    src = tmp_path / "source.txt"
    src.touch()

    # WHEN copy_with_callback is called with same source and destination
    # THEN verify SameFileError is raised
    with pytest.raises(errors.SameFileError):
        copy_with_callback(src, src)


def test_copy_with_callback_invalid_callback(tmp_path):
    """Verify copy_with_callback raises ValueError for non-callable callback."""
    # GIVEN source and destination paths with invalid callback
    src = tmp_path / "source.txt"
    dest = tmp_path / "destination.txt"
    src.touch()
    invalid_callback = "not a callable"

    # WHEN copy_with_callback is called with non-callable callback
    # THEN verify ValueError is raised
    with pytest.raises(ValueError, match="callback must be callable"):
        copy_with_callback(src, dest, invalid_callback)


def test_copyfileobj():
    """Verify _copyfileobj correctly copies data and invokes callback."""
    # GIVEN source and destination buffers with sample data
    src_data = b"Hello World" * 100
    src = io.BytesIO(src_data)
    dest = io.BytesIO()
    callback = MagicMock()
    length = 20

    # WHEN _copyfileobj is called
    _copyfileobj(src, dest, callback, length)

    # THEN verify data was copied correctly
    assert dest.getvalue() == src_data
    # AND verify callback was called expected number of times
    expected_calls = len(src_data) // length
    assert callback.call_count == expected_calls


def test_tmp_to_output_1(tmp_path):
    """Verify tmp_to_output handles file naming and overwrite scenarios correctly."""
    # GIVEN temporary source file
    tmp_file = tmp_path / "test.txt"
    tmp_file.touch()

    # WHEN tmp_to_output is called first time
    result = tmp_to_output(tmp_file, "test_filename")

    # THEN verify expected file path is returned
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename.txt"
    assert result.exists()
    assert result.is_file()

    # WHEN tmp_to_output is called second time
    result = tmp_to_output(tmp_file, "test_filename")

    # THEN verify incremented filename is returned
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename_1.txt"
    assert result.exists()
    assert result.is_file()

    # WHEN tmp_to_output is called with overwrite flag
    result = tmp_to_output(tmp_file, "test_filename", overwrite=True)

    # THEN verify original filename is returned
    assert isinstance(result, Path)
    assert result == tmp_path / "test_filename.txt"
    assert result.exists()
    assert result.is_file()


def test_tmp_to_output_2(tmp_path):
    """Verify tmp_to_output handles custom output path correctly."""
    # GIVEN temporary source file
    tmp_file = tmp_path / "test.txt"
    tmp_file.touch()

    # WHEN tmp_to_output is called with custom output path
    result = tmp_to_output(tmp_file, "test_filename", new_file=tmp_path / "test" / "new_file.txt")

    # THEN verify custom path is returned
    assert isinstance(result, Path)
    assert result == tmp_path / "test" / "new_file.txt"
    assert result.exists()
    assert result.is_file()


def test_existing_file_path_1(tmp_path):
    """Verify existing_file_path returns path for existing file."""
    # GIVEN existing file
    file = tmp_path / "test.txt"
    file.touch()

    # WHEN existing_file_path is called
    # THEN verify file path is returned
    assert existing_file_path(file) == file


def test_existing_file_path_2(tmp_path):
    """Verify existing_file_path raises BadParameter for non-existent file."""
    # GIVEN non-existent file path
    file = tmp_path / "test2.txt"

    # WHEN existing_file_path is called
    # THEN verify BadParameter is raised
    with pytest.raises(typer.BadParameter):
        existing_file_path(file)


def test_existing_file_path_3(tmp_path):
    """Verify existing_file_path raises BadParameter for directory path."""
    # GIVEN existing directory path
    directory = tmp_path / "test_dir"
    directory.mkdir()

    # WHEN existing_file_path is called
    # THEN verify BadParameter is raised
    with pytest.raises(typer.BadParameter):
        existing_file_path(directory)
