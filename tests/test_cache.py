"""Test cache subcommand."""

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Create a temporary cache directory with test files."""
    # Given: A temporary cache directory
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # And: A test file in the cache directory
    test_file = cache_dir / "test.txt"
    test_file.touch()

    # And: A directory with a test file
    test_dir = cache_dir / "test_dir"
    test_dir.mkdir()
    test_file = test_dir / "test.txt"
    test_file.touch()

    return cache_dir


def test_cache_list(tmp_cache_dir, clean_stdout, debug):
    """Verify cache list displays directory tree structure."""
    # Given: Cache directory path in settings
    args = ["cache"]
    settings.update({"cache_dir": tmp_cache_dir})

    # When: Invoking cache list command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Output contains expected tree structure
    output = clean_stdout()
    assert exc_info.value.code == 0
    assert "│   └── 📄 " in output
    assert "├── 📂 " in output
    assert "└── 📄 " in output


def test_cache_clean(tmp_cache_dir, clean_stdout, debug):
    """Verify cache clean removes all cached files."""
    # Given: Cache directory path in settings
    args = ["cache", "-c"]
    settings.update({"cache_dir": tmp_cache_dir})

    # When: Invoking cache clean command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Success message is displayed
    output = clean_stdout()
    assert exc_info.value.code == 0
    assert "Success: Cache cleared" in output

    # And: Cache directory is empty
    assert not any(tmp_cache_dir.iterdir())
