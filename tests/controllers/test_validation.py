"""Test the video file validator behind the `check` subcommand."""

import contextlib
from dataclasses import replace

import pytest
from box import Box

from vid_cleaner.constants import CodecTypes, VerifyLevel
from vid_cleaner.controllers.validation import (
    CLEAN_DURATION_TOLERANCE_SECONDS,
    CLIP_DURATION_TOLERANCE_SECONDS,
    OUTPUT_DECODE_REASON,
    OUTPUT_DURATION_REASON,
    OUTPUT_NO_DURATION_REASON,
    OUTPUT_NO_VIDEO_REASON,
    OUTPUT_STREAM_COUNT_REASON,
    OUTPUT_UNREADABLE_REASON,
    ExpectedOutput,
    SourceLength,
    StreamCounts,
    check_video_file,
    expected_clean_output,
    expected_clip_output,
    probe_duration_seconds,
    verify_output,
)
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.models.conversion_plan import ConversionPlan, OutputStream
from vid_cleaner.models.video_file import VideoFile


def test_valid_file_passes(video_path, mock_video_probe):
    """Verify a probeable file carrying a video stream is reported valid."""
    # Given: A file that probes cleanly and has a real video stream
    mock_video_probe("reference.json")

    # When: Checking it
    result = check_video_file(video_path)

    # Then: It passes with no reason
    assert result.ok is True
    assert result.reason is None
    assert result.path == video_path


def test_missing_file_fails(tmp_path):
    """Verify a path that does not exist fails before any probe is attempted."""
    # Given: A path with nothing at it
    missing = tmp_path / "missing.mkv"

    # When: Checking it
    result = check_video_file(missing)

    # Then: It fails with an existence reason
    assert result.ok is False
    assert result.reason == "file does not exist"


def test_directory_fails(tmp_path):
    """Verify a directory carrying a video extension is not treated as a file."""
    # Given: A directory named like a video file
    directory = tmp_path / "season.mkv"
    directory.mkdir()

    # When: Checking it
    result = check_video_file(directory)

    # Then: It fails rather than being probed
    assert result.ok is False
    assert result.reason == "file does not exist"


def test_non_video_extension_fails(tmp_path):
    """Verify a real file without a video container extension fails."""
    # Given: A text file
    notes = tmp_path / "notes.txt"
    notes.write_text("not a video")

    # When: Checking it
    result = check_video_file(notes)

    # Then: It fails on its extension
    assert result.ok is False
    assert result.reason == "not a video file"


def test_unprobeable_file_reports_ffprobe_reason(video_path, mocker):
    """Verify ffprobe's own diagnostic is surfaced as the failure reason."""
    # Given: A file ffprobe refuses to read
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(path=video_path, reason="moov atom not found"),
    )

    # When: Checking it
    result = check_video_file(video_path)

    # Then: The reason is ffprobe's, not a generic message
    assert result.ok is False
    assert result.reason == "moov atom not found"


def test_unlaunchable_ffprobe_fails_the_file_not_the_run(video_path, mocker):
    """Verify an ffprobe that cannot run is reported as a verdict rather than raised."""
    # Given: ffprobe is missing from PATH
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=FileNotFoundError(2, "No such file or directory", "ffprobe"),
    )

    # When: Checking the file
    result = check_video_file(video_path)

    # Then: The batch gets a verdict naming the missing binary
    assert result.ok is False
    assert result.reason is not None
    assert "ffprobe" in result.reason


def test_audio_only_file_fails(video_path, mock_video_probe):
    """Verify a file that probes cleanly but carries no video stream fails."""
    # Given: A file whose only stream is audio
    mock_video_probe("audio_only.json")

    # When: Checking it
    result = check_video_file(video_path)

    # Then: It fails for lacking video
    assert result.ok is False
    assert result.reason == "no video stream"


def test_cover_art_does_not_count_as_video(video_path, mock_ffprobe_box, mocker):
    """Verify an embedded poster cannot pass a file that has no real video stream.

    Containers routinely carry cover art as an mjpeg or png "video" stream, so a raw
    codec_type test would wave an audio-only file through.
    """
    # Given: An audio-only file with an mjpeg cover art stream
    probe_box = mock_ffprobe_box("audio_only.json")
    probe_box.streams.append(
        Box(
            {
                "index": 1,
                "codec_name": "mjpeg",
                "codec_type": CodecTypes.VIDEO,
                "tags": {},
            },
            default_box=True,
            default_box_create_on_get=False,
        )
    )
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=probe_box,
    )

    # When: Checking it
    result = check_video_file(video_path)

    # Then: The cover art does not rescue it
    assert result.ok is False
    assert result.reason == "no video stream"


def test_deep_check_runs_full_decode(video_path, mocker, mock_video_probe):
    """Verify `--deep` decodes the file and passes when ffmpeg reports nothing."""
    # Given: A file that probes cleanly and decodes without error
    mock_video_probe("reference.json")
    decode = mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value=None,
    )

    # When: Checking it deeply
    result = check_video_file(video_path, deep=True)

    # Then: The decode ran and the file passed
    assert result.ok is True
    decode.assert_called_once_with(video_path)


def test_deep_check_reports_decode_error(video_path, mocker, mock_video_probe):
    """Verify a file with a clean header but a corrupt body fails under `--deep`."""
    # Given: A file that probes cleanly but fails to decode
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value="Error while decoding stream #0:0",
    )

    # When: Checking it deeply
    result = check_video_file(video_path, deep=True)

    # Then: ffmpeg's decode error is the reason
    assert result.ok is False
    assert result.reason == "Error while decoding stream #0:0"


def test_shallow_check_never_decodes(video_path, mocker, mock_video_probe):
    """Verify the default tier stays metadata-only so a library scan stays fast."""
    # Given: A file that probes cleanly
    mock_video_probe("reference.json")
    decode = mocker.patch("vid_cleaner.controllers.validation.decode_check")

    # When: Checking it without `--deep`
    check_video_file(video_path)

    # Then: No decode was attempted
    decode.assert_not_called()


@pytest.mark.parametrize("suffix", [".mkv", ".MKV", ".mp4", ".avi"])
def test_extension_matching_is_case_insensitive(tmp_path, suffix, mock_video_probe):
    """Verify an uppercase container extension is accepted."""
    # Given: A file whose extension differs only in case
    path = tmp_path / f"video{suffix}"
    path.touch()
    mock_video_probe("reference.json")

    # When: Checking it
    result = check_video_file(path)

    # Then: The extension does not disqualify it
    assert result.ok is True


@pytest.fixture
def reference_expected(video_path, mock_video_probe):
    """Build the expectation that matches `reference.json` exactly, with its probe box."""
    box = mock_video_probe("reference.json")
    source = VideoFile(video_path)
    expected = ExpectedOutput(
        streams=StreamCounts.of_video(source),
        duration_seconds=probe_duration_seconds(source),
        tolerance_seconds=CLEAN_DURATION_TOLERANCE_SECONDS,
    )
    return expected, box


def _without_video(expected, box):
    box.streams = [s for s in box.streams if s.codec_type != CodecTypes.VIDEO]
    return expected


def _without_duration(expected, box):
    box.duration = None
    return expected


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        pytest.param(lambda e, b: e, None, id="unchanged"),
        pytest.param(
            lambda e, b: replace(e, duration_seconds=e.duration_seconds + 1.5),
            None,
            id="in-tolerance",
        ),
        pytest.param(
            lambda e, b: replace(e, duration_seconds=e.duration_seconds + 5),
            OUTPUT_DURATION_REASON,
            id="too-short",
        ),
        pytest.param(
            lambda e, b: replace(e, streams=replace(e.streams, audio=e.streams.audio + 1)),
            OUTPUT_STREAM_COUNT_REASON,
            id="missing-audio",
        ),
        pytest.param(_without_video, OUTPUT_NO_VIDEO_REASON, id="no-video"),
        pytest.param(_without_duration, OUTPUT_NO_DURATION_REASON, id="no-output-duration"),
        pytest.param(
            lambda e, b: replace(e, duration_seconds=None), None, id="no-expected-duration"
        ),
    ],
)
def test_verify_output_quick(reference_expected, video_path, change, reason):
    """Verify the quick check passes a matching output and names the first mismatch."""
    # Given: An expectation and probe box derived from the same file, then altered
    expected, box = reference_expected
    expected = change(expected, box)

    # When/Then: Verifying passes or raises the matching reason
    context = contextlib.nullcontext() if reason is None else pytest.raises(VideoCleanError)
    with context as exc_info:
        verify_output(
            video_path, source_path=video_path, expected=expected, level=VerifyLevel.QUICK
        )
    if reason is not None:
        assert exc_info is not None
        assert reason in exc_info.value.reason


def test_verify_output_unreadable(reference_expected, video_path, mock_unreadable_probe):
    """Verify an output ffprobe rejects fails with the probe reason attached."""
    # Given: An output ffprobe cannot read
    expected, _ = reference_expected
    mock_unreadable_probe(video_path.name)

    # When/Then: Verifying raises
    with pytest.raises(VideoCleanError) as exc_info:
        verify_output(
            video_path, source_path=video_path, expected=expected, level=VerifyLevel.QUICK
        )
    assert exc_info.value.reason.startswith(OUTPUT_UNREADABLE_REASON)


def test_verify_output_off_skips_probe(video_path, mocker):
    """Verify the off level returns without probing the output."""
    # Given: A probe spy
    expected = ExpectedOutput(
        streams=StreamCounts(video=1, audio=1, subtitle=0),
        duration_seconds=None,
        tolerance_seconds=2.0,
    )
    probe = mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", autospec=True)

    # When: Verifying at the off level
    verify_output(video_path, source_path=video_path, expected=expected, level=VerifyLevel.OFF)

    # Then: Nothing was probed
    probe.assert_not_called()


def test_verify_output_full_decode(reference_expected, video_path, mocker):
    """Verify only the full level decodes, and a decode error fails the output."""
    # Given: A decode that reports an error
    expected, _ = reference_expected
    decode = mocker.patch(
        "vid_cleaner.controllers.validation.decode_check", return_value="bad frame"
    )

    # When/Then: Full fails with the decode error as the end of the reason
    with pytest.raises(VideoCleanError) as exc_info:
        verify_output(video_path, source_path=video_path, expected=expected, level=VerifyLevel.FULL)
    assert exc_info.value.reason.startswith(OUTPUT_DECODE_REASON)
    assert exc_info.value.reason.endswith("bad frame")

    # And: A clean decode passes
    decode.return_value = None
    verify_output(video_path, source_path=video_path, expected=expected, level=VerifyLevel.FULL)

    # And: Quick never decodes
    decode.reset_mock()
    verify_output(video_path, source_path=video_path, expected=expected, level=VerifyLevel.QUICK)
    decode.assert_not_called()


@pytest.mark.parametrize(
    ("source_seconds", "start", "duration", "expected_seconds"),
    [
        (7200, "00:10:00", "00:01:30", 90),
        (630, "00:10:00", "00:01:00", 30),
        (300, "00:10:00", "00:01:00", 0),
        (None, "00:10:00", "00:01:00", 60),
    ],
)
def test_expected_clip_output_duration(
    video_path, mock_video_probe, source_seconds, start, duration, expected_seconds
):
    """Verify the expected clip length is cut short when the clip runs past the source end."""
    # Given: A source of the given length
    box = mock_video_probe("reference.json")
    box.duration = None if source_seconds is None else str(source_seconds)

    # When: Computing the expectation
    expected = expected_clip_output(VideoFile(video_path), start=start, duration=duration)

    # Then: The length and tolerance match
    assert expected.duration_seconds == expected_seconds
    assert expected.tolerance_seconds == CLIP_DURATION_TOLERANCE_SECONDS
    assert expected.streams == StreamCounts.of_video(VideoFile(video_path))


@pytest.mark.parametrize(
    ("source_length", "expected_seconds"),
    [
        pytest.param(
            SourceLength(
                container_seconds=130.0,
                stream_seconds={0: 120.0, 1: 120.0, 2: 119.0, 3: 121.0, 4: 130.0},
            ),
            121.0,
            id="dropped-stream-runs-longer",
        ),
        pytest.param(
            SourceLength(container_seconds=130.0, stream_seconds={0: 120.0, 1: 120.0}),
            130.0,
            id="kept-stream-without-length",
        ),
        pytest.param(None, None, id="no-source-length"),
    ],
)
def test_expected_clean_output(source_length, expected_seconds):
    """Verify a clean expects the plan's stream counts at the length of the kept streams."""
    # Given: A plan mapping one video, two audio and one subtitle stream, dropping stream 4
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=0, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=1, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=2, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=3, codec_type=CodecTypes.SUBTITLE),
        ]
    )

    # When: Computing the expectation
    expected = expected_clean_output(plan, source_length=source_length)

    # Then: Counts come from the plan, and the length from the streams it keeps
    assert expected.streams == StreamCounts(video=1, audio=2, subtitle=1)
    assert expected.duration_seconds == expected_seconds
    assert expected.tolerance_seconds == CLEAN_DURATION_TOLERANCE_SECONDS


def test_source_length_reads_stream_lengths(video_path, mock_video_probe):
    """Verify per-stream lengths come from the Matroska tag, then ffprobe's own value."""
    # Given: A Matroska probe whose subtitles carry both a tag and an estimated duration
    box = mock_video_probe("reference.json")
    box.streams[1].tags = {}
    box.streams[1].duration = "58.5"

    # When: Reading the source lengths
    length = SourceLength.of_video(VideoFile(video_path))

    # Then: The tag wins, the plain duration is the fallback, and the container is kept
    assert length.container_seconds == pytest.approx(60.268)
    assert length.stream_seconds[0] == pytest.approx(60.226)
    assert length.stream_seconds[1] == pytest.approx(58.5)
    assert length.stream_seconds[6] == pytest.approx(59.393)
