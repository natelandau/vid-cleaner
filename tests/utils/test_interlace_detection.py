"""Tests for interlace detection."""

from pathlib import Path

import pytest
from nclutils.sh import CompletedCommand, ShellCommandError, ShellCommandTimeoutError
from pytest_mock import MockerFixture

from vid_cleaner.utils.interlace_detection import (
    UNDECIDED_SCAN,
    FieldOrder,
    InterlaceScan,
    detect_interlacing,
    parse_idet_output,
)

IDET_FIXTURES = Path(__file__).parent.parent / "fixtures" / "idet"


@pytest.mark.parametrize(
    ("fixture", "interlaced", "field_order"),
    [
        ("interlaced_tff.txt", True, FieldOrder.TFF),
        ("interlaced_bff.txt", True, FieldOrder.BFF),
        ("telecined.txt", True, FieldOrder.TFF),
        ("progressive.txt", False, None),
        ("too_few_frames.txt", None, None),
        ("no_counts.txt", None, None),
    ],
)
def test_parse_idet_output_verdict(fixture, interlaced, field_order):
    """Verify the verdict and field order come from the summed multi-frame counts."""
    # Given recorded idet stderr
    stderr = (IDET_FIXTURES / fixture).read_text()

    # When parsing it
    scan = parse_idet_output(stderr)

    # Then the verdict matches
    assert scan == InterlaceScan(interlaced=interlaced, field_order=field_order)


@pytest.mark.parametrize(
    ("field_order", "description"),
    [(FieldOrder.TFF, "top field first"), (FieldOrder.BFF, "bottom field first")],
)
def test_field_order_description(field_order, description):
    """Verify each field order reads as plain words."""
    assert field_order.description == description


@pytest.mark.parametrize(
    ("interlaced", "field_order"),
    [
        pytest.param(True, None, id="interlaced without field order"),
        pytest.param(False, FieldOrder.TFF, id="progressive with field order"),
        pytest.param(None, FieldOrder.BFF, id="undecided with field order"),
    ],
)
def test_interlace_scan_rejects_inconsistent_field_order(interlaced, field_order):
    """Verify a field order exists exactly when the scan found interlacing."""
    with pytest.raises(ValueError, match="field_order"):
        InterlaceScan(interlaced=interlaced, field_order=field_order)


@pytest.mark.parametrize(
    ("scan", "description"),
    [
        (
            InterlaceScan(interlaced=True, field_order=FieldOrder.TFF),
            "interlaced (top field first)",
        ),
        (InterlaceScan(interlaced=False, field_order=None), "progressive"),
        (UNDECIDED_SCAN, "undetermined"),
    ],
)
def test_interlace_scan_description(scan, description):
    """Verify each verdict reads as plain words, with the field order when interlaced."""
    assert scan.description == description


def _completed(mocker: MockerFixture, stderr: str) -> CompletedCommand:
    """Build a stand-in for a finished ffmpeg run that printed the given stderr."""
    result = mocker.MagicMock(spec=CompletedCommand)
    result.stderr_lines = stderr.splitlines()
    return result


def test_detect_interlacing_without_duration_scans_from_start(mocker):
    """Verify an unknown duration scans one run from the start and parses its stderr."""
    # Given ffmpeg output for an interlaced file
    stderr = (IDET_FIXTURES / "interlaced_tff.txt").read_text()
    run = mocker.patch(
        "vid_cleaner.utils.interlace_detection.run_command",
        return_value=_completed(mocker, stderr),
    )

    # When detecting without a duration
    scan = detect_interlacing(Path("movie.mkv"), duration_seconds=None)

    # Then one command ran without seeking, and the verdict comes from its output
    run.assert_called_once()
    assert "-ss" not in run.call_args.args[0]
    assert scan == InterlaceScan(interlaced=True, field_order=FieldOrder.TFF)


def test_detect_interlacing_samples_three_points_of_a_long_file(mocker):
    """Verify a long file is sampled at 25%, 50%, and 75% of its duration."""
    run = mocker.patch(
        "vid_cleaner.utils.interlace_detection.run_command",
        return_value=_completed(mocker, (IDET_FIXTURES / "progressive.txt").read_text()),
    )

    scan = detect_interlacing(Path("movie.mkv"), duration_seconds=4000.0)

    seeks = [call.args[0][call.args[0].index("-ss") + 1] for call in run.call_args_list]
    assert seeks == ["1000.0", "2000.0", "3000.0"]
    assert scan.interlaced is False


def _timed_out_scan() -> ShellCommandTimeoutError:
    """Build the error nclutils raises when the scan's ffmpeg outlives its timeout."""
    command = CompletedCommand(
        argv=("ffmpeg",), returncode=-9, stdout="", stderr="", duration=60.0, cwd=None
    )
    return ShellCommandTimeoutError(result=command, timeout=60.0)


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ShellCommandError("ffmpeg failed"), id="ffmpeg failed"),
        pytest.param(OSError("ffmpeg missing"), id="ffmpeg missing"),
        pytest.param(_timed_out_scan(), id="ffmpeg timed out"),
    ],
)
def test_detect_interlacing_returns_undecided_when_ffmpeg_fails(mocker, error):
    """Verify a failed or timed-out scan yields an undecided verdict."""
    # Given ffmpeg that fails, is missing, or runs past the timeout
    mocker.patch("vid_cleaner.utils.interlace_detection.run_command", side_effect=error)

    # When detecting
    scan = detect_interlacing(Path("movie.mkv"), duration_seconds=None)

    # Then the verdict is undecided
    assert scan == UNDECIDED_SCAN


def test_detect_interlacing_returns_undecided_without_idet_counts(mocker):
    """Verify ffmpeg output with no idet counts yields an undecided verdict."""
    # Given ffmpeg that prints no idet counts
    mocker.patch(
        "vid_cleaner.utils.interlace_detection.run_command",
        return_value=_completed(mocker, "no counts here"),
    )

    # When detecting
    scan = detect_interlacing(Path("movie.mkv"), duration_seconds=None)

    # Then the verdict is undecided
    assert scan == UNDECIDED_SCAN


def test_detect_interlacing_bounds_each_ffmpeg_run(mocker):
    """Verify every scan command has a timeout and reads only the first real video stream."""
    # Given ffmpeg that returns progressive counts
    run = mocker.patch(
        "vid_cleaner.utils.interlace_detection.run_command",
        return_value=_completed(mocker, (IDET_FIXTURES / "progressive.txt").read_text()),
    )

    # When sampling a long file
    detect_interlacing(Path("movie.mkv"), duration_seconds=4000.0)

    # Then each run has a timeout and maps a video stream that excludes cover art
    for call in run.call_args_list:
        command = call.args[0]
        assert command[command.index("-map") + 1] == "0:V:0"
        assert call.kwargs["timeout"] > 0
