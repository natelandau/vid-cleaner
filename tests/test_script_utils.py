"""Test shared script utilities."""

from collections.abc import Callable
from pathlib import Path

import pytest

from vid_cleaner.utils import copy_file, unique_filename


def test_unique_filename(tmp_path: Path) -> None:
    """Verify unique filename generation when file does not exist."""
    # Given: A path to a non-existent file
    path = tmp_path / "file.txt"

    # When: Generating a unique filename
    result = unique_filename(path)

    # Then: Original path is returned unchanged
    assert result == path


def test_unique_filename_with_existing_file(tmp_path: Path) -> None:
    """Verify unique filename generation when file exists."""
    # Given: An existing file
    path = tmp_path / "file.txt"
    path.touch()

    # When: Generating a unique filename
    result = unique_filename(tmp_path / "file.txt")

    # Then: Incremented filename is returned
    assert result == tmp_path / "file_1.txt"


def test_unique_filename_with_existing_file_and_separator(tmp_path: Path) -> None:
    """Verify unique filename generation with custom separator."""
    # Given: Multiple existing files with incremented names
    path = tmp_path / "file.txt"
    path.touch()
    (tmp_path / "file-1.txt").touch()
    (tmp_path / "file-2.txt").touch()

    # When: Generating a unique filename with custom separator
    result = unique_filename(path, separator="-")

    # Then: Next available incremented filename is returned
    assert result == tmp_path / "file-3.txt"


def test_unique_filename_with_has_separator_one(tmp_path: Path) -> None:
    """Verify unique filename generation when filename already contains separator."""
    # Given: Multiple existing files with incremented names
    path = tmp_path / "file.txt"
    path.touch()
    (tmp_path / "file.txt").touch()
    (tmp_path / "file_1.txt").touch()
    (tmp_path / "file_2.txt").touch()

    # When: Generating unique filename with has_separator flag
    result = unique_filename(path, continue_sequence=True)

    # Then: Next available incremented filename is returned
    assert result == tmp_path / "file_3.txt"


def test_unique_filename_with_has_separator_two(tmp_path: Path) -> None:
    """Verify unique filename generation when input filename contains number."""
    # Given: Multiple existing files including numbered filename
    path = tmp_path / "file_2.txt"
    path.touch()
    (tmp_path / "file.txt").touch()
    (tmp_path / "file_1.txt").touch()

    # When: Generating unique filename with has_separator flag
    result = unique_filename(path, continue_sequence=True)

    # Then: Next available incremented filename is returned
    assert result == tmp_path / "file_3.txt"


def test_unique_filename_with_double_suffix(tmp_path: Path) -> None:
    """Verify unique filename generation with multiple file extensions."""
    # Given: Multiple existing files with double extensions
    path = tmp_path / "file.txt.bak"
    path.touch()
    (tmp_path / "file.txt").touch()
    (tmp_path / "file_1.txt").touch()
    (tmp_path / "file_2.txt").touch()

    # When: Generating unique filename
    result = unique_filename(path)

    # Then: Incremented filename preserves both extensions
    assert result == tmp_path / "file.txt_1.bak"


def test_copy_file_file_not_found(tmp_path: Path):
    """Verify copy_file raises FileNotFoundError when source file doesn't exist."""
    # Given: Source and destination paths
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"

    # When/Then: Copying non-existent file raises error
    with pytest.raises(FileNotFoundError):
        copy_file(src, dst)


def test_copy_file_not_transient(
    tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable
):
    """Verify copy_file shows progress bar when transient=False."""
    # Given: Source file with content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")

    # When: Copying file with visible progress bar
    copy_file(src, dst, transient=False, with_progress=True)
    output = clean_stdout()

    # Then: Progress bar was displayed and file was copied
    assert "Copy test.txt… " in output
    assert "100%" in output
    assert dst.read_text() == "Hello, world!"


def test_copy_file_transient(tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable):
    """Verify copy_file hides progress bar when transient=True."""
    # Given: Source file with content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")

    # When: Copying file with hidden progress bar
    copy_file(src, dst, transient=True, with_progress=True)
    output = clean_stdout()

    # Then: Progress bar was hidden and file was copied
    assert output == "\n"
    assert dst.read_text() == "Hello, world!"


def test_copy_file_overwrite(tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable):
    """Verify copy_file overwrites existing destination file."""
    # Given: Source and destination files with content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")
    dst.write_text("Old content")

    # When: Copying file and overwriting destination
    copy_file(src, dst, overwrite=True)

    assert dst.read_text() == "Hello, world!"


def test_copy_file_no_overwrite(
    tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable
):
    """Verify copy_file overwrites existing destination file."""
    # Given: Source and destination files with content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")
    dst.write_text("Old content")
    new_dst = unique_filename(dst)

    # When: Copying file and overwriting destination
    copy_file(src, dst, overwrite=False)

    assert dst.read_text() == "Old content"
    assert new_dst.read_text() == "Hello, world!"


def test_copy_file_same_file(tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable):
    """Verify copy_file handles same file as destination."""
    # Given: Source and destination files with same content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")

    # When: Copying file to itself
    copy_file(src, src)
    output = clean_stdout()

    # Then: No progress bar was displayed
    assert "Did not copy" in output.replace("\n", "")
    assert not dst.exists()
    assert src.exists()


def test_copy_directory(tmp_path: Path):
    """Verify copy_file raises error when copying directory."""
    # Given: Source directory with files
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "file1.txt").write_text("Hello, world!")
    (src / "file2.txt").write_text("Hello, world!")

    # When: Attempting to copy directory
    with pytest.raises(FileNotFoundError):
        copy_file(src, dst)

    # Then: Error is raised (handled by pytest.raises)


def test_copy_file_with_no_progress(
    tmp_path: Path, clean_stdout: pytest.CaptureFixture, debug: Callable
):
    """Verify copy_file works without progress bar."""
    # Given: Source file with content
    src = tmp_path / "test.txt"
    dst = tmp_path / "test_copy.txt"
    src.write_text("Hello, world!")

    # When: Copying file without progress bar
    copy_file(src, dst, with_progress=False, transient=False)
    output = clean_stdout()

    # Then: File is copied without progress output
    assert not output
    assert src.read_text() == "Hello, world!"
    assert dst.read_text() == "Hello, world!"
