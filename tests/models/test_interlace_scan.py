"""Tests for `VideoFile.interlace_scan`."""

import pytest

from vid_cleaner.utils import FieldOrder, InterlaceScan, ProbeCache

TOP_FIELD_FIRST_SCAN = InterlaceScan(interlaced=True, field_order=FieldOrder.TFF)


def test_interlace_scan_runs_detection_once_per_file(make_video, mock_interlace_detection):
    """Verify repeated scans of one file reuse the first verdict."""
    mock_interlace_detection.return_value = TOP_FIELD_FIRST_SCAN
    video = make_video("reference.json")

    assert video.interlace_scan() == TOP_FIELD_FIRST_SCAN
    assert video.interlace_scan() == TOP_FIELD_FIRST_SCAN
    assert mock_interlace_detection.call_count == 1


def test_interlace_scan_reuses_a_cached_verdict(make_video, mock_interlace_detection, tmp_path):
    """Verify a stored verdict for an unchanged file skips detection."""
    video = make_video("reference.json")
    stat = video.path.stat()
    with ProbeCache(tmp_path / "cache") as cache:
        cache.put_scan(
            video.path, size=stat.st_size, mtime_ns=stat.st_mtime_ns, scan=TOP_FIELD_FIRST_SCAN
        )

        assert video.interlace_scan(cache) == TOP_FIELD_FIRST_SCAN

    mock_interlace_detection.assert_not_called()


def test_interlace_scan_stores_its_verdict(make_video, mock_interlace_detection, tmp_path):
    """Verify a fresh scan is written to the cache for the next run."""
    mock_interlace_detection.return_value = TOP_FIELD_FIRST_SCAN
    video = make_video("reference.json")
    stat = video.path.stat()
    with ProbeCache(tmp_path / "cache") as cache:
        video.interlace_scan(cache)

        stored = cache.get_scan(video.path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    assert stored == TOP_FIELD_FIRST_SCAN


@pytest.mark.parametrize(
    ("probed_duration", "expected_duration"),
    [("600.000000", 600.0), (None, None), ("0", None), ("N/A", None)],
)
def test_interlace_scan_passes_probe_duration(
    make_video, mock_interlace_detection, probed_duration, expected_duration
):
    """Verify the detector gets the probed duration, or None when it is unusable."""
    video = make_video("reference.json")
    video.probe_box.duration = probed_duration

    video.interlace_scan()

    mock_interlace_detection.assert_called_once_with(video.path, duration_seconds=expected_duration)
