"""Test the cache of ffprobe results."""

import pytest

from vid_cleaner.utils.probe_cache import (
    PROBE_CACHE_FILENAME,
    PROBE_CACHE_UNUSABLE_WARNING,
    ProbeCache,
)

PROBE = {"format": {"filename": "movie.mkv"}, "streams": [{"codec_type": "video"}]}


@pytest.mark.parametrize(
    ("size", "mtime_ns", "hit"),
    [
        pytest.param(100, 5, True, id="unchanged"),
        pytest.param(101, 5, False, id="size changed"),
        pytest.param(100, 6, False, id="modified"),
    ],
)
def test_lookup_hits_only_an_unchanged_file(tmp_path, size, mtime_ns, hit) -> None:
    """Verify a stored probe is returned only while the file's size and modification time match."""
    # Given: A probe stored for a file of size 100 modified at 5
    path = tmp_path / "movie.mkv"
    with ProbeCache(tmp_path / "cache") as cache:
        cache.put(path, size=100, mtime_ns=5, probe=PROBE)

    # When: A later run looks the file up
    with ProbeCache(tmp_path / "cache") as cache:
        result = cache.get(path, size=size, mtime_ns=mtime_ns)

    # Then: Only an unchanged file reuses the stored probe
    assert result == (PROBE if hit else None)


def test_storing_again_replaces_the_entry(tmp_path) -> None:
    """Verify a re-probed file replaces its old entry instead of adding another."""
    # Given: A probe stored for an earlier version of a file
    path = tmp_path / "movie.mkv"
    changed = {**PROBE, "format": {"filename": "changed.mkv"}}
    with ProbeCache(tmp_path) as cache:
        cache.put(path, size=100, mtime_ns=5, probe=PROBE)

        # When: The changed file's probe is stored
        cache.put(path, size=200, mtime_ns=9, probe=changed)

        # Then: Only the new version is cached
        assert cache.get(path, size=200, mtime_ns=9) == changed
        assert cache.get(path, size=100, mtime_ns=5) is None


def test_unusable_database_turns_the_cache_off(tmp_path, capsys) -> None:
    """Verify a cache that cannot be opened warns and misses instead of failing the run."""
    # Given: A database path that is a directory, so it cannot be opened
    (tmp_path / PROBE_CACHE_FILENAME).mkdir()
    cache = ProbeCache(tmp_path)
    path = tmp_path / "movie.mkv"

    # When: Using the cache
    cache.put(path, size=100, mtime_ns=5, probe=PROBE)
    result = cache.get(path, size=100, mtime_ns=5)

    # Then: Every lookup misses and the user is told once
    assert result is None
    assert capsys.readouterr().err.count(PROBE_CACHE_UNUSABLE_WARNING) == 1


def test_closed_cache_stores_nothing(tmp_path) -> None:
    """Verify a probe that finishes after the cache closes is not written."""
    # Given: A cache whose `with` block has ended
    path = tmp_path / "movie.mkv"
    with ProbeCache(tmp_path) as cache:
        pass

    # When: A late probe stores its result
    cache.put(path, size=100, mtime_ns=5, probe=PROBE)

    # Then: A later run finds nothing for it
    with ProbeCache(tmp_path) as later:
        assert later.get(path, size=100, mtime_ns=5) is None


def test_closing_drops_entries_unused_for_90_days(tmp_path, monkeypatch) -> None:
    """Verify entries for files no run has looked up in 90 days are removed, and used ones stay."""
    day = 24 * 60 * 60
    clock = {"now": 1_000 * day}
    monkeypatch.setattr("vid_cleaner.utils.probe_cache.time.time", lambda: clock["now"])
    kept = tmp_path / "kept.mkv"
    deleted = tmp_path / "deleted.mkv"

    # Given: Two files cached, and only one looked up again 89 days later
    with ProbeCache(tmp_path) as cache:
        cache.put(kept, size=1, mtime_ns=1, probe=PROBE)
        cache.put(deleted, size=1, mtime_ns=1, probe=PROBE)
    clock["now"] += 89 * day
    with ProbeCache(tmp_path) as cache:
        cache.get(kept, size=1, mtime_ns=1)

    # When: A run of another directory closes the cache 100 days after the files were cached
    clock["now"] += 11 * day
    with ProbeCache(tmp_path) as cache:
        cache.get(tmp_path / "other.mkv", size=1, mtime_ns=1)
    with ProbeCache(tmp_path) as cache:
        kept_result = cache.get(kept, size=1, mtime_ns=1)
        deleted_result = cache.get(deleted, size=1, mtime_ns=1)

    # Then: Only the entry no run used in 90 days is gone
    assert kept_result == PROBE
    assert deleted_result is None
