# type: ignore
"""Test helpers."""

from pathlib import Path

import pytest

from vid_cleaner.utils import pp
from vid_cleaner.utils.filesystem_utils import unique_filename


@pytest.fixture(autouse=True)
def _change_test_dir(monkeypatch, tmp_path) -> None:
    """All tests should run in a temporary directory."""
    monkeypatch.chdir(tmp_path)


def test_all_styles_displays_styles(capsys) -> None:
    """Display all available print styles."""
    # Given: A configured pretty print instance
    pp.configure(debug=True, trace=True)

    # When: Displaying all styles
    pp.all_styles()

    # Then: Output contains style information
    captured = capsys.readouterr()
    assert "Available styles" in captured.out
    assert "info" in captured.out
    assert "debug" in captured.out
    assert "trace" in captured.out
    assert "success" in captured.out
    assert "warning" in captured.out
    assert "error" in captured.out

    # Reset the print styles
    pp.configure(debug=False, trace=False)


def test_unique_filename(tmp_path: Path) -> None:
    """Generate a unique filename by appending an incrementing number if the file already exists."""
    path = tmp_path / "file.txt"

    # When: Generating a unique filename
    result = unique_filename(path)
    assert result == path


def test_unique_filename_with_existing_file(tmp_path: Path) -> None:
    """Generate a unique filename by appending an incrementing number if the file already exists."""
    path = tmp_path / "file.txt"
    path.touch()

    # When: Generating a unique filename
    result = unique_filename(tmp_path / "file.txt")
    assert result == tmp_path / "file_1.txt"


def test_unique_filename_with_existing_file_and_separator(tmp_path: Path) -> None:
    """Generate a unique filename by appending an incrementing number if the file already exists."""
    path = tmp_path / "file.txt"
    path.touch()
    (tmp_path / "file-1.txt").touch()
    (tmp_path / "file-2.txt").touch()

    # When: Generating a unique filename
    result = unique_filename(path, separator="-")
    assert result == tmp_path / "file-3.txt"
