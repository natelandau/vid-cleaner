"""Tests for `VideoFile.interlace_scan`."""

import pytest

from vid_cleaner.utils import FieldOrder, InterlaceScan, ProbeCache
from vid_cleaner.utils.interlace_detection import UNDECIDED_SCAN, InterlaceScanError

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


def test_interlace_scan_does_not_store_a_failed_scan(
    make_video, mock_interlace_detection, tmp_path
):
    """Verify a scan ffmpeg could not finish is undecided now and retried on the next run."""
    video = make_video("reference.json")
    mock_interlace_detection.side_effect = InterlaceScanError(path=video.path, reason="timed out")
    stat = video.path.stat()
    with ProbeCache(tmp_path / "cache") as cache:
        scan = video.interlace_scan(cache)

        stored = cache.get_scan(video.path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)

    assert scan == UNDECIDED_SCAN
    assert stored is None


@pytest.mark.parametrize(
    ("format_duration", "stream_duration", "expected_duration"),
    [
        pytest.param("600.000000", None, 600.0, id="format duration"),
        pytest.param(None, "590.5", 590.5, id="stream duration when format has none"),
        pytest.param(None, None, None, id="missing"),
        pytest.param("0", None, None, id="zero"),
        pytest.param("N/A", None, None, id="unparsable"),
        pytest.param("nan", None, None, id="not a number"),
        pytest.param("inf", None, None, id="infinite"),
        pytest.param("-5", None, None, id="negative"),
    ],
)
def test_interlace_scan_passes_probe_duration(
    make_video, mock_interlace_detection, format_duration, stream_duration, expected_duration
):
    """Verify the detector gets the probed duration, or None when it is unusable."""
    video = make_video("reference.json")
    video.probe_box.duration = format_duration
    video.video_streams[0].duration = stream_duration

    video.interlace_scan()

    mock_interlace_detection.assert_called_once_with(video.path, duration_seconds=expected_duration)


def test_interlace_scan_reads_duration_from_the_source(
    make_video, mock_interlace_detection, mocker, tmp_path
):
    """Verify a scan after a temporary output exists still samples by the source's duration."""
    # Given: A file whose latest temporary output is shorter than the source
    video = make_video("reference.json")
    source_box = video.probe_box
    source_box.duration = "600.0"
    temp_output = tmp_path / "temp.mkv"
    temp_box = source_box.copy()
    temp_box.path_to_file = temp_output
    temp_box.duration = "30.0"
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=lambda path, **_: temp_box if path == temp_output else source_box,
    )
    mocker.patch.object(video.temp_file, "latest_temp_path", return_value=temp_output)
    assert video.probe_box.duration == "30.0"

    # When: Scanning it
    video.interlace_scan()

    # Then: The detector gets the source's duration
    mock_interlace_detection.assert_called_once_with(video.path, duration_seconds=600.0)
