"""Decide whether a file on disk is a workable video file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from vid_cleaner.constants import VIDEO_SUFFIXES, CodecTypes, VerifyLevel
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils.ffmpeg_utils import decode_check

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from box import Box

    from vid_cleaner.models.conversion_plan import ConversionPlan
    from vid_cleaner.models.video_file import VideoFile

CLEAN_DURATION_TOLERANCE_SECONDS = 2.0
CLIP_DURATION_TOLERANCE_SECONDS = 10.0

OUTPUT_NO_VIDEO_REASON = "output has no video stream"
OUTPUT_STREAM_COUNT_REASON = "stream(s), expected"
OUTPUT_DURATION_REASON = "long, expected"
OUTPUT_NO_DURATION_REASON = "output has no duration"
OUTPUT_UNREADABLE_REASON = "output cannot be read"
OUTPUT_DECODE_REASON = "output failed to decode"

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class CheckResult:
    """The verdict on one file, carrying enough to print a line and set an exit code."""

    path: Path
    ok: bool
    reason: str | None = None


def check_probed_video(video: VideoFile, *, deep: bool = False) -> CheckResult:
    """Apply the checks that need a successful probe to an already-probed file.

    Split from `check_video_file` so discovery mode, which has already probed every file
    it returns, can finish the verdict without paying for a second probe.

    Args:
        video (VideoFile): A file whose probe has already succeeded.
        deep (bool): Decode every frame instead of trusting the header.

    Returns:
        CheckResult: The verdict for this file.
    """
    # `video_streams` excludes cover art (mjpeg/png), so an audio-only file carrying an
    # embedded poster cannot pass on the strength of that poster alone.
    if not video.video_streams:
        return CheckResult(path=video.path, ok=False, reason="no video stream")

    if deep and (error := decode_check(video.path)):
        return CheckResult(path=video.path, ok=False, reason=error)

    return CheckResult(path=video.path, ok=True)


def check_video_file(path: Path, *, deep: bool = False) -> CheckResult:
    """Report whether one path is a video file the rest of the tool could work on.

    Run the cheapest disqualifying check first so a directory of junk costs no ffprobe
    calls, and return a verdict rather than raising so a caller can check a whole batch
    and report every failure at once.

    Args:
        path (Path): The path to check.
        deep (bool): Decode every frame instead of trusting the header. Minutes per file.

    Returns:
        CheckResult: The verdict for this path.
    """
    # Imported here because `vid_cleaner.models.video_file` imports this package at module
    # scope; a top-level import makes `vid_cleaner.models` unimportable on its own.
    from vid_cleaner.models.video_file import VideoFile  # noqa: PLC0415

    resolved = path.expanduser().resolve()

    if not resolved.is_file():
        return CheckResult(path=path, ok=False, reason="file does not exist")

    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        return CheckResult(path=path, ok=False, reason="not a video file")

    # `VideoFile` probes lazily, so the probe this catches happens inside the call below.
    # OSError covers an ffprobe that cannot be launched at all: reporting it per file keeps
    # the batch printing one line per path instead of aborting on a traceback.
    try:
        result = check_probed_video(VideoFile(resolved), deep=deep)
    except VideoProbeError as e:
        return CheckResult(path=path, ok=False, reason=e.reason)
    except OSError as e:
        return CheckResult(path=path, ok=False, reason=str(e))

    # Report against the path the user named, not the resolved one, so the printed line
    # matches what they typed.
    return CheckResult(path=path, ok=result.ok, reason=result.reason)


@dataclass(frozen=True)
class StreamCounts:
    """How many streams of each type a file holds, attachments and cover art excluded."""

    video: int
    audio: int
    subtitle: int

    @classmethod
    def of_video(cls, video: VideoFile) -> StreamCounts:
        """Count a probed file's streams, so a copy of it can be checked against the source.

        Args:
            video (VideoFile): A file whose probe has already succeeded.

        Returns:
            StreamCounts: The counts, with cover art excluded from video.
        """
        return cls(
            video=len(video.video_streams),
            audio=len(video.audio_streams),
            subtitle=len(video.subtitle_streams),
        )

    @classmethod
    def of_plan(cls, plan: ConversionPlan) -> StreamCounts:
        """Count the streams a plan maps, so the output can be checked against the plan.

        Args:
            plan (ConversionPlan): The conversion plan about to run.

        Returns:
            StreamCounts: The planned counts per stream type.
        """
        return cls(
            video=len(plan.streams_of(CodecTypes.VIDEO)),
            audio=len(plan.streams_of(CodecTypes.AUDIO)),
            subtitle=len(plan.streams_of(CodecTypes.SUBTITLE)),
        )


@dataclass(frozen=True)
class ExpectedOutput:
    """What a finished output file must look like to replace its source.

    Attributes:
        streams (StreamCounts): The stream counts the output must have.
        duration_seconds (float | None): The expected length, or None to skip the check.
        tolerance_seconds (float): How far the output length may differ from the expected one.
    """

    streams: StreamCounts
    duration_seconds: float | None
    tolerance_seconds: float


def probe_duration_seconds(video: VideoFile) -> float | None:
    """Read a file's length so it can be compared after ffmpeg runs.

    Args:
        video (VideoFile): The file to read.

    Returns:
        float | None: The length in seconds, or None when the probe reports none.
    """
    duration = video.probe_box.duration
    return float(duration) if duration else None


def _hms_to_seconds(value: str) -> float:
    """Convert an `HH:MM:SS` string to seconds."""
    hours, minutes, seconds = (float(part) for part in value.split(":"))
    return hours * SECONDS_PER_HOUR + minutes * SECONDS_PER_MINUTE + seconds


def _stream_duration_seconds(stream: Box) -> float | None:
    """Read one stream's length from its Matroska `DURATION` tag or ffprobe's `duration`."""
    try:
        # Matroska tags the exact per-track length; ffprobe's own value there is estimated
        for key, value in stream.tags.items():
            if str(key).upper().startswith("DURATION") and isinstance(value, str) and value:
                return _hms_to_seconds(value)
        if stream.duration:
            return float(stream.duration)
    except ValueError:
        # A malformed value leaves the length unknown, so the caller uses the container's
        return None
    return None


@dataclass(frozen=True)
class SourceLength:
    """A source's lengths, read before ffmpeg runs, since its probe then reads the output.

    Attributes:
        container_seconds (float | None): The container's length, or None when unknown.
        stream_seconds (Mapping[int, float]): Each stream's length by source index, for the
            streams that report one.
    """

    container_seconds: float | None
    stream_seconds: Mapping[int, float]

    @classmethod
    def of_video(cls, video: VideoFile) -> SourceLength:
        """Read a file's container and per-stream lengths, to compare a clean's output against.

        Args:
            video (VideoFile): The source, before ffmpeg writes a temp file for it.

        Returns:
            SourceLength: The lengths the probe reports.
        """
        return cls(
            container_seconds=probe_duration_seconds(video),
            stream_seconds={
                stream.index: seconds
                for stream in video.probe_box.streams
                if (seconds := _stream_duration_seconds(stream)) is not None
            },
        )

    def of_streams(self, source_indexes: Collection[int]) -> float | None:
        """Return the length an output holding only these source streams should have.

        The container length spans every stream, so a dropped track that runs longer than
        the kept ones would make a correct output look truncated.

        Args:
            source_indexes (Collection[int]): The source indexes the output keeps.

        Returns:
            float | None: The longest kept stream, or the container length when any kept
                stream reports no length of its own.
        """
        if source_indexes and all(index in self.stream_seconds for index in source_indexes):
            return max(self.stream_seconds[index] for index in source_indexes)
        return self.container_seconds


def expected_clean_output(
    plan: ConversionPlan, *, source_length: SourceLength | None
) -> ExpectedOutput:
    """Describe the output a clean should produce: the planned streams at their source length.

    Args:
        plan (ConversionPlan): The conversion plan about to run.
        source_length (SourceLength | None): The source lengths read before ffmpeg ran, or
            None to skip the length check.

    Returns:
        ExpectedOutput: The expectation to pass to `verify_output`.
    """
    duration_seconds = (
        None
        if source_length is None
        else source_length.of_streams({stream.source_index for stream in plan.streams})
    )
    return ExpectedOutput(
        streams=StreamCounts.of_plan(plan),
        duration_seconds=duration_seconds,
        tolerance_seconds=CLEAN_DURATION_TOLERANCE_SECONDS,
    )


def expected_clip_output(source: VideoFile, *, start: str, duration: str) -> ExpectedOutput:
    """Describe the output a clip should produce, shortened when the clip runs past the end.

    Read the source before ffmpeg runs: once a temp file exists, the source probes it instead.

    Args:
        source (VideoFile): The file being clipped.
        start (str): Clip start as `HH:MM:SS`.
        duration (str): Clip length as `HH:MM:SS`.

    Returns:
        ExpectedOutput: The expectation to pass to `verify_output`.
    """
    requested = _hms_to_seconds(duration)
    source_duration = probe_duration_seconds(source)
    expected = (
        requested
        if source_duration is None
        else min(requested, max(0.0, source_duration - _hms_to_seconds(start)))
    )
    return ExpectedOutput(
        streams=StreamCounts.of_video(source),
        duration_seconds=expected,
        tolerance_seconds=CLIP_DURATION_TOLERANCE_SECONDS,
    )


def _first_failure(output: VideoFile, expected: ExpectedOutput) -> str | None:
    """Return the first reason the probed output misses the expectation, or None."""
    if not output.video_streams:
        return OUTPUT_NO_VIDEO_REASON

    actual = StreamCounts.of_video(output)
    comparisons = (
        ("video", actual.video, expected.streams.video),
        ("audio", actual.audio, expected.streams.audio),
        ("subtitle", actual.subtitle, expected.streams.subtitle),
    )
    for kind, found, wanted in comparisons:
        if found != wanted:
            return f"output has {found} {kind} {OUTPUT_STREAM_COUNT_REASON} {wanted}"

    if expected.duration_seconds is not None:
        wanted_text = str(timedelta(seconds=round(expected.duration_seconds)))
        actual_duration = probe_duration_seconds(output)
        if actual_duration is None:
            return f"{OUTPUT_NO_DURATION_REASON}, expected {wanted_text}"
        if abs(actual_duration - expected.duration_seconds) > expected.tolerance_seconds:
            return (
                f"output is {timedelta(seconds=round(actual_duration))!s} "
                f"{OUTPUT_DURATION_REASON} {wanted_text}"
            )
    return None


def verify_output(
    output_path: Path, *, source_path: Path, expected: ExpectedOutput, level: VerifyLevel
) -> None:
    """Check an ffmpeg output before it replaces the original, so a bad run loses nothing.

    Args:
        output_path (Path): The file ffmpeg wrote.
        source_path (Path): The original, named in the raised error.
        expected (ExpectedOutput): The streams and length the output must have.
        level (VerifyLevel): `OFF` skips every check; `QUICK` probes; `FULL` also decodes.

    Raises:
        VideoCleanError: When the output fails a check; the reason says which.
    """
    if level == VerifyLevel.OFF:
        return

    # Imported here for the same cycle as in `check_video_file`.
    from vid_cleaner.models.video_file import VideoFile  # noqa: PLC0415

    try:
        reason = _first_failure(output=VideoFile(output_path), expected=expected)
    except VideoProbeError as e:
        reason = f"{OUTPUT_UNREADABLE_REASON}: {e.reason}"

    if reason is None and level == VerifyLevel.FULL and (error := decode_check(output_path)):
        reason = f"{OUTPUT_DECODE_REASON}: {error}"

    if reason is not None:
        raise VideoCleanError(path=source_path, reason=reason)
