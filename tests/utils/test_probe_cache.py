"""Test the cache of ffprobe results."""

import pytest

from vid_cleaner.utils.interlace_detection import UNDECIDED_SCAN, FieldOrder, InterlaceScan
from vid_cleaner.utils.probe_cache import (
    PROBE_CACHE_FILENAME,
    PROBE_CACHE_UNUSABLE_WARNING,
    ProbeCache,
)

PROBE = {"format": {"filename": "movie.mkv"}, "streams": [{"codec_type": "video"}]}
SCAN = InterlaceScan(interlaced=True, field_order=FieldOrder.BFF)


def put_probe(cache, path, **stat) -> None:
    """Store the sample probe."""
    cache.put(path, probe=PROBE, **stat)


def put_scan(cache, path, **stat) -> None:
    """Store the sample scan."""
    cache.put_scan(path, scan=SCAN, **stat)


# Each kind of entry: how to store it, how to look it up, and what comes back
ENTRY_KINDS = [
    pytest.param(put_probe, ProbeCache.get, PROBE, id="probe"),
    pytest.param(put_scan, ProbeCache.get_scan, SCAN, id="scan"),
]


@pytest.mark.parametrize(("store", "lookup", "stored"), ENTRY_KINDS)
@pytest.mark.parametrize(
    ("size", "mtime_ns", "hit"),
    [
        pytest.param(100, 5, True, id="unchanged"),
        pytest.param(101, 5, False, id="size changed"),
        pytest.param(100, 6, False, id="modified"),
    ],
)
def test_lookup_hits_only_an_unchanged_file(
    tmp_path, store, lookup, stored, size, mtime_ns, hit
) -> None:
    """Verify a stored probe is returned only while the file's size and modification time match."""
    # Given: A probe stored for a file of size 100 modified at 5
    path = tmp_path / "movie.mkv"
    with ProbeCache(tmp_path / "cache") as cache:
        store(cache, path, size=100, mtime_ns=5)

    # When: A later run looks the file up
    with ProbeCache(tmp_path / "cache") as cache:
        result = lookup(cache, path, size=size, mtime_ns=mtime_ns)

    # Then: Only an unchanged file reuses the stored entry
    assert result == (stored if hit else None)


def test_undecided_scan_is_cached(tmp_path) -> None:
    """Verify an undecided verdict is a hit, so the file is not scanned again."""
    # Given: An undecided verdict stored for a file
    path = tmp_path / "movie.mkv"
    with ProbeCache(tmp_path) as cache:
        cache.put_scan(path, size=1, mtime_ns=1, scan=UNDECIDED_SCAN)

    # When: A later run looks the file up
    with ProbeCache(tmp_path) as cache:
        result = cache.get_scan(path, size=1, mtime_ns=1)

    # Then: The verdict comes back instead of a miss
    assert result == UNDECIDED_SCAN


def test_scan_and_probe_entries_are_independent(tmp_path) -> None:
    """Verify storing one kind of entry for a file does not answer lookups of the other."""
    # Given: Only a scan stored for a file
    path = tmp_path / "movie.mkv"
    with ProbeCache(tmp_path) as cache:
        cache.put_scan(path, size=1, mtime_ns=1, scan=SCAN)

        # When/Then: The probe lookup misses
        assert cache.get(path, size=1, mtime_ns=1) is None


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
    cache.put_scan(path, size=100, mtime_ns=5, scan=SCAN)
    scan_result = cache.get_scan(path, size=100, mtime_ns=5)

    # Then: Every lookup misses and the user is told once
    assert result is None
    assert scan_result is None
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
        for path in (kept, deleted):
            cache.put(path, size=1, mtime_ns=1, probe=PROBE)
            cache.put_scan(path, size=1, mtime_ns=1, scan=SCAN)
    clock["now"] += 89 * day
    with ProbeCache(tmp_path) as cache:
        cache.get(kept, size=1, mtime_ns=1)
        cache.get_scan(kept, size=1, mtime_ns=1)

    # When: A run of another directory closes the cache 100 days after the files were cached
    clock["now"] += 11 * day
    with ProbeCache(tmp_path) as cache:
        cache.get(tmp_path / "other.mkv", size=1, mtime_ns=1)
    with ProbeCache(tmp_path) as cache:
        kept_results = (
            cache.get(kept, size=1, mtime_ns=1),
            cache.get_scan(kept, size=1, mtime_ns=1),
        )
        deleted_results = (
            cache.get(deleted, size=1, mtime_ns=1),
            cache.get_scan(deleted, size=1, mtime_ns=1),
        )

    # Then: Only the entries no run used in 90 days are gone
    assert kept_results == (PROBE, SCAN)
    assert deleted_results == (None, None)
