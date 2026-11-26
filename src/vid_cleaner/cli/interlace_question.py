"""Ask once, before any encode, how a clean run handles interlaced video."""

from dataclasses import dataclass, field, replace
from pathlib import Path

from nclutils import pp
from rich.prompt import Prompt
from rich.text import Text

from vid_cleaner.config import RunOptions
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    InterlaceScan,
    ProbeCache,
    ask_if_interactive,
    stop_run,
    track_progress,
)

from vid_cleaner.models.video_file import VideoFile  # isort: skip

DEINTERLACE_FLAG_REQUIRED_ERROR = (
    "Some files are interlaced and this run cannot ask how to handle them. "
    "Pass `--deinterlace` or `--no-deinterlace` to decide in advance."
)
UNDECIDED_INTERLACE_REASON = (
    "interlaced video with no decision on deinterlacing; "
    "pass `--deinterlace` or `--no-deinterlace` to decide in advance"
)
INTERLACED_FILES_SKIPPED_REASON = "interlaced video, skipped at the user's request"
INTERLACE_SCAN_FAILED_WARNING = "Could not check for interlacing:"
INTERLACE_QUESTION = "These files are interlaced. How should they be encoded?"
DEINTERLACE_CHOICE = "deinterlace"
REENCODE_CHOICE = "re-encode"
SKIP_CHOICE = "skip"


@dataclass(frozen=True)
class InterlaceDecision:
    """The files a clean run processes and the options it processes them with.

    Attributes:
        files (list[VideoFile]): The files to clean, in order.
        options (RunOptions): The options for every file not in `deinterlaced`, with
            `deinterlace` decided when the user answered the question. Read a file's
            options through `options_for`.
        skipped (list[VideoFile]): Interlaced files the user chose not to clean.
        deinterlaced_paths (frozenset[Path]): Paths of the interlaced files the user chose
            to deinterlace.
    """

    files: list[VideoFile]
    options: RunOptions
    skipped: list[VideoFile] = field(default_factory=list)
    deinterlaced_paths: frozenset[Path] = frozenset()

    def options_for(self, video_file: VideoFile) -> RunOptions:
        """Return the options to clean one file with.

        Deinterlacing re-encodes lossily, so an answer of "deinterlace" applies only to the
        files the question listed, never to a file the scan did not check.

        Args:
            video_file (VideoFile): A file from `files`.

        Returns:
            RunOptions: The run's options, with `deinterlace=True` for a listed file.
        """
        if video_file.path in self.deinterlaced_paths:
            return replace(self.options, deinterlace=True)
        return self.options


def _scan_for_interlacing(
    video_files: list[VideoFile], *, options: RunOptions, cache_dir: Path
) -> list[tuple[VideoFile, InterlaceScan]]:
    """Scan the files the run may re-encode and return the interlaced ones with their verdicts.

    A file that cannot be probed or scanned is left for `clean_one_file`, which reports
    the failure in its usual place.

    Args:
        video_files (list[VideoFile]): The files the run will clean.
        options (RunOptions): The CLI-only values for this run.
        cache_dir (Path): The directory that holds the probe cache.

    Returns:
        list[tuple[VideoFile, InterlaceScan]]: The files whose scan found interlacing, in
            run order.
    """
    candidates = [
        video_file
        for video_file in video_files
        if options.deinterlace is True or _will_reencode_video(video_file, options=options)
    ]
    if not candidates:
        return []

    interlaced: list[tuple[VideoFile, InterlaceScan]] = []
    with ProbeCache(cache_dir) as probe_cache:
        for video_file in track_progress(
            candidates, description=f"Checking {len(candidates)} files for interlacing..."
        ):
            try:
                scan = video_file.interlace_scan(probe_cache)
            except (VideoCleanError, VideoProbeError, OSError) as e:
                # `Text` keeps a `:name:` in a filename from turning into an emoji
                pp.warning(Text(f"{INTERLACE_SCAN_FAILED_WARNING} {video_file.path}: {e}"))
                continue
            if scan.interlaced:
                interlaced.append((video_file, scan))
    return interlaced


def _will_reencode_video(video_file: VideoFile, *, options: RunOptions) -> bool:
    """Check whether the run re-encodes the file, treating an unreadable file as no.

    Args:
        video_file (VideoFile): The file to check.
        options (RunOptions): The CLI-only values for this run.

    Returns:
        bool: True when the requested work re-encodes the file's video.
    """
    try:
        return video_file.will_reencode_video(options)
    # OSError covers an ffprobe that cannot be launched; `clean_one_file` reports it per file
    except (VideoCleanError, VideoProbeError, OSError) as e:
        pp.debug(f"Could not tell whether {video_file.path} is re-encoded: {e}")
        return False


def _ask_how_to_handle_interlaced_files() -> str:
    """Ask the user what to do with the interlaced files.

    Returns:
        str: One of `DEINTERLACE_CHOICE`, `REENCODE_CHOICE`, or `SKIP_CHOICE`.

    Raises:
        RunStopped: When the console cannot prompt.
    """
    answer = ask_if_interactive(
        lambda: Prompt.ask(
            INTERLACE_QUESTION,
            choices=[DEINTERLACE_CHOICE, REENCODE_CHOICE, SKIP_CHOICE],
            default=DEINTERLACE_CHOICE,
            console=pp.console(),
        )
    )
    if answer is None:
        raise stop_run(DEINTERLACE_FLAG_REQUIRED_ERROR)
    return answer


def resolve_interlaced_files(
    video_files: list[VideoFile], *, options: RunOptions, cache_dir: Path, assume_yes: bool
) -> InterlaceDecision:
    """Decide, once and before any encode, how a clean run handles interlaced video.

    Re-encoding interlaced video without deinterlacing it bakes combing into the output,
    so a run that would do that asks first. A batch can run for hours, so the question is
    asked once for the whole run rather than per file.

    Args:
        video_files (list[VideoFile]): The files the run will clean.
        options (RunOptions): The CLI-only values for this run.
        cache_dir (Path): The directory that holds the probe cache, which stores scan results.
        assume_yes (bool): The user passed `--yes`, so the run must not prompt.

    Returns:
        InterlaceDecision: The files to clean, the options to clean them with, and the files
            the user chose to skip.

    Raises:
        RunStopped: When interlaced files need a decision and the run cannot ask for one.
    """
    unchanged = InterlaceDecision(files=video_files, options=options)
    if options.deinterlace is False:
        return unchanged

    interlaced = _scan_for_interlacing(video_files, options=options, cache_dir=cache_dir)
    if options.deinterlace is True or not interlaced:
        return unchanged

    pp.warning(
        "Interlaced video found:",
        details=[Text(f"{video_file.path}: {scan.description}") for video_file, scan in interlaced],
    )
    # The planner notes on each dry-run plan that a real run asks
    if options.dryrun:
        return unchanged
    if assume_yes:
        raise stop_run(DEINTERLACE_FLAG_REQUIRED_ERROR)

    answer = _ask_how_to_handle_interlaced_files()
    # Files outside the list were not found interlaced, so they never deinterlace and the
    # planner never scans them mid-batch
    run_options = replace(options, deinterlace=False)
    interlaced_files = [video_file for video_file, _ in interlaced]
    interlaced_paths = frozenset(video_file.path for video_file in interlaced_files)
    skip = answer == SKIP_CHOICE
    return InterlaceDecision(
        files=[video_file for video_file in video_files if video_file.path not in interlaced_paths]
        if skip
        else video_files,
        options=run_options,
        skipped=interlaced_files if skip else [],
        deinterlaced_paths=interlaced_paths if answer == DEINTERLACE_CHOICE else frozenset(),
    )
