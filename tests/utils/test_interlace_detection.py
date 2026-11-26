"""Tests for interlace detection."""

from pathlib import Path

import pytest
from nclutils.sh import CompletedCommand, ShellCommandError

from vid_cleaner.utils.interlace_detection import (
    FieldOrder,
    InterlaceScan,
    detect_interlacing,
    parse_idet_output,
)

IDET_FIXTURES = Path(__file__).parent.parent / "fixtures" / "idet"
UNDECIDED = InterlaceScan(interlaced=None, field_order=None)


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


def _completed(mocker, stderr: str):
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


@pytest.mark.parametrize(
    "outcome",
    [
        ShellCommandError("ffmpeg failed"),
        OSError("ffmpeg missing"),
        "no counts here",
    ],
)
def test_detect_interlacing_returns_undecided_when_ffmpeg_fails(mocker, outcome):
    """Verify a failed or unreadable scan yields an undecided verdict."""
    # Given ffmpeg that fails or prints no idet counts
    if isinstance(outcome, Exception):
        mocker.patch("vid_cleaner.utils.interlace_detection.run_command", side_effect=outcome)
    else:
        mocker.patch(
            "vid_cleaner.utils.interlace_detection.run_command",
            return_value=_completed(mocker, outcome),
        )

    # When detecting
    scan = detect_interlacing(Path("movie.mkv"), duration_seconds=None)

    # Then the verdict is undecided
    assert scan == UNDECIDED
