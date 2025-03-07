"""Test cache subcommand."""

import cappa
import pytest

from vid_cleaner.utils import settings
from vid_cleaner.vidcleaner import VidCleaner


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Create a cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create a test file in the cache directory
    test_file = cache_dir / "test.txt"
    test_file.touch()

    # create a directory with a test file
    test_dir = cache_dir / "test_dir"
    test_dir.mkdir()
    test_file = test_dir / "test.txt"
    test_file.touch()

    return cache_dir


def test_cache_list(tmp_cache_dir, clean_stdout, debug):
    """Test cache list subcommand."""
    args = ["cache"]

    settings.update({"cache_dir": tmp_cache_dir})

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    # debug(output, "output")

    assert exc_info.value.code == 0
    assert "│   └── 📄 " in output
    assert "├── 📂 " in output
    assert "└── 📄 " in output


def test_cache_clean(tmp_cache_dir, clean_stdout, debug):
    """Test cache clean subcommand."""
    args = ["cache", "-c"]

    settings.update({"cache_dir": tmp_cache_dir})

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    # debug(output, "output")

    assert exc_info.value.code == 0
    assert "Success: Cache cleared" in output

    assert not any(tmp_cache_dir.iterdir())
