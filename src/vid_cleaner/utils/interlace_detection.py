"""Detect interlaced video by sampling it with ffmpeg's idet filter."""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from nclutils import pp
from nclutils.sh import ShellCommandError, run_command

IDET_MIN_DECIDED_FRAMES = 100
IDET_INTERLACED_RATIO = 0.25
SAMPLE_POSITIONS = (0.25, 0.5, 0.75)
SAMPLE_FRAMES = 150
FALLBACK_SAMPLE_FRAMES = 450
# Shorter files cannot hold three non-overlapping samples of SAMPLE_FRAMES at typical frame rates
MIN_SAMPLED_DURATION_SECONDS = 60.0
# A sample decodes a few hundred frames, so a run this long means ffmpeg is stuck
SCAN_TIMEOUT_SECONDS = 60

INTERLACED_DESCRIPTION_TEMPLATE = "interlaced ({field_order})"
PROGRESSIVE_DESCRIPTION = "progressive"
UNDECIDED_DESCRIPTION = "undetermined"

# Only the multi-frame lines are trusted; single-frame and repeated-field lines are noisier
_MULTI_FRAME_PATTERN = re.compile(
    r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)"
)


class FieldOrder(StrEnum):
    """The order in which an interlaced video stores its two fields."""

    TFF = "tff"
    BFF = "bff"

    @property
    def description(self) -> str:
        """Return the field order in plain words for messages to the user."""
        return "top field first" if self is FieldOrder.TFF else "bottom field first"


@dataclass(frozen=True)
class InterlaceScan:
    """The outcome of scanning a video for interlacing.

    Attributes:
        interlaced (bool | None): True or False when the scan decided, None when it could not.
        field_order (FieldOrder | None): Set only when `interlaced` is True.
    """

    interlaced: bool | None
    field_order: FieldOrder | None

    def __post_init__(self) -> None:
        """Reject a verdict whose field order disagrees with whether it found interlacing.

        Raises:
            ValueError: When `field_order` is missing from an interlaced verdict, or set on
                one that is not interlaced.
        """
        if (self.interlaced is True) != (self.field_order is not None):
            msg = (
                f"InterlaceScan needs a field_order exactly when interlaced is True, got "
                f"interlaced={self.interlaced!r} and field_order={self.field_order!r}"
            )
            raise ValueError(msg)

    @property
    def description(self) -> str:
        """Describe the verdict in plain words for messages to the user.

        Returns:
            str: `interlaced (<field order>)`, `progressive`, or `undetermined`.
        """
        if self.interlaced is None:
            return UNDECIDED_DESCRIPTION
        if self.field_order is None:
            return PROGRESSIVE_DESCRIPTION
        return INTERLACED_DESCRIPTION_TEMPLATE.format(field_order=self.field_order.description)


UNDECIDED_SCAN = InterlaceScan(interlaced=None, field_order=None)


def parse_idet_output(stderr: str) -> InterlaceScan:
    """Turn ffmpeg idet stderr into a verdict so callers can warn or deinterlace.

    Multi-frame counts from every sample are summed. The video counts as interlaced when
    TFF and BFF frames reach `IDET_INTERLACED_RATIO` of the frames idet could classify.

    Args:
        stderr (str): The ffmpeg stderr from one or more idet runs.

    Returns:
        InterlaceScan: Undecided when fewer than `IDET_MIN_DECIDED_FRAMES` frames were classified.
    """
    top_first = bottom_first = progressive = 0
    for match in _MULTI_FRAME_PATTERN.finditer(stderr):
        top_first += int(match.group(1))
        bottom_first += int(match.group(2))
        progressive += int(match.group(3))

    interlaced_frames = top_first + bottom_first
    decided_frames = interlaced_frames + progressive
    if decided_frames < IDET_MIN_DECIDED_FRAMES:
        return UNDECIDED_SCAN

    if interlaced_frames / decided_frames < IDET_INTERLACED_RATIO:
        return InterlaceScan(interlaced=False, field_order=None)

    field_order = FieldOrder.TFF if top_first >= bottom_first else FieldOrder.BFF
    return InterlaceScan(interlaced=True, field_order=field_order)


def _build_sample_command(path: Path, *, start_seconds: float | None, frames: int) -> list[str]:
    """Build the ffmpeg command that runs idet over a run of frames."""
    # `-ss` before `-i` seeks without decoding the skipped part
    seek = ["-ss", str(start_seconds)] if start_seconds is not None else []
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        *seek,
        "-i",
        str(path),
        "-frames:v",
        str(frames),
        "-map",
        # Capital V skips attached pictures such as cover art
        "0:V:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        "idet",
        "-f",
        "null",
        "-",
    ]


def detect_interlacing(path: Path, *, duration_seconds: float | None) -> InterlaceScan:
    """Scan a video for interlacing so the caller can warn or deinterlace it.

    Sample three points of a long file. Scan the start of a file whose duration is unknown
    or too short to sample.

    Args:
        path (Path): The video file to scan.
        duration_seconds (float | None): The video's duration, when known.

    Returns:
        InterlaceScan: Undecided when ffmpeg fails, times out, or classifies too few frames.
    """
    if duration_seconds is None or duration_seconds < MIN_SAMPLED_DURATION_SECONDS:
        commands = [_build_sample_command(path, start_seconds=None, frames=FALLBACK_SAMPLE_FRAMES)]
    else:
        commands = [
            _build_sample_command(
                path, start_seconds=round(duration_seconds * position, 3), frames=SAMPLE_FRAMES
            )
            for position in SAMPLE_POSITIONS
        ]

    stderr_lines: list[str] = []
    try:
        for command in commands:
            stderr_lines.extend(
                run_command(command, check=False, timeout=SCAN_TIMEOUT_SECONDS).stderr_lines
            )
    # ShellCommandError includes ShellCommandTimeoutError, raised when a run hits the timeout
    except (ShellCommandError, OSError) as e:
        pp.debug(f"Interlace scan failed for {path}: {e}")
        return UNDECIDED_SCAN

    return parse_idet_output("\n".join(stderr_lines))
