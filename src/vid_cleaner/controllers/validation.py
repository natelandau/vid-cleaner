"""Decide whether a file on disk is a workable video file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from vid_cleaner.constants import VideoContainerTypes
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils.ffmpeg_utils import decode_check

if TYPE_CHECKING:
    from vid_cleaner.models.video_file import VideoFile


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

    if resolved.suffix.lower() not in {container.value for container in VideoContainerTypes}:
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
