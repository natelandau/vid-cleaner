"""Clean subcommand."""

import math
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Any

import cappa
from cappa import Dep
from nclutils import pp
from rich.filesize import decimal
from rich.prompt import Confirm
from rich.text import Text

from vid_cleaner.cli.discovery_output import (
    FROM_WITH_FILES_ERROR,
    NO_FILES_OR_FROM_ERROR,
    REQUIRES_FROM_ERROR,
    confirm_selection,
    discover_from_directory,
    present_discovery,
)
from vid_cleaner.cli.interlace_question import (
    INTERLACED_FILES_SKIPPED_REASON,
    UNDECIDED_INTERLACE_REASON,
    resolve_interlaced_files,
)
from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import (
    FHD_RESOLUTION,
    INTERLACE_QUESTION_NOTICE,
    SCALE_TO_1080P_LABEL,
    SYMBOL_CROSS,
    UPSCALE_DECLINED_REASON,
    CleanStatus,
    VerifyLevel,
)
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    TEMP_CLEANUP_WARNING,
    FfmpegCommand,
    OutputWrite,
    ProbeCache,
    ask_if_interactive,
    coerce_video_files,
    output_write_messages,
    render_operations,
    render_substeps,
    resolve_out_path_override,
    stop_run,
)
from vid_cleaner.vidcleaner import (
    CleanCommand,
    VidCleaner,
    build_config,
    build_run_options,
    json_report_for,
)
from vid_cleaner.views.json_output import clean_outcome_entry, file_object, report_on_exit

from vid_cleaner.cli.ffmpeg_step import (  # isort: skip
    copy_result_to_output,
    run_ffmpeg_step,
    run_verify_step,
    verification_enabled,
    verified_message,
)
from vid_cleaner.controllers.validation import (  # isort: skip
    SourceLength,
    expected_clean_output,
)
from vid_cleaner.models.conversion_plan import ConversionPlan, PlanAction  # isort: skip
from vid_cleaner.models.video_file import VideoFile  # isort: skip

H265_AND_VP9_ERROR = "Cannot convert to both H265 and VP9"
ORIGINAL_REMOVAL_WARNING = "Warning: could not remove the original"
RESULT_PROBE_WARNING = "Warning: could not read the written file"
FILES_FAILED_ERROR = "file(s) failed"
NOT_ENOUGH_SPACE_ERROR = "not enough free space"
# Upscaling, an added downmix track, or re-encoding a low-bitrate source can make the
# output larger than the source, so each copy of the result is budgeted above source size.
OUTPUT_SIZE_MARGIN = 1.5


@dataclass
class CleanOutcome:
    """Collect what happened to one file so the human output and the JSON report agree.

    Attributes:
        video_file (VideoFile): The file processed.
        status (CleanStatus): How the clean ended. A file stays `FAILED` until it finishes.
        original (dict[str, Any] | None): The input file as it was before the run, or None
            when the caller did not ask for it or the probe failed.
        result (dict[str, Any] | None): The written file, or None when nothing was written,
            the caller did not ask for it, or the written file could not be read.
        plan (ConversionPlan | None): The conversion plan, or None when planning failed.
        destination (Path | None): Where the run writes or would write the result. Set
            only for `CLEANED` and `PLANNED`.
        ffmpeg_command (list[str] | None): The ffmpeg command line that ran or was
            previewed, or None when ffmpeg did not run.
        write (OutputWrite | None): The finished write, or None when nothing was written.
        verify (VerifyLevel | None): The level of the output check that passed, or None
            when no check ran.
        warnings (list[str]): Problems after a successful write, such as a temp file or an
            original that could not be removed.
        reason (str | None): Why the file failed, or None.
    """

    video_file: VideoFile
    status: CleanStatus
    original: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    plan: ConversionPlan | None = None
    destination: Path | None = None
    ffmpeg_command: list[str] | None = None
    write: OutputWrite | None = None
    verify: VerifyLevel | None = None
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None


def write_output(
    video_file: VideoFile,
    *,
    destination: Path,
    overwrite: bool,
    keep_original: bool,
) -> tuple[OutputWrite | None, list[str]]:
    """Copy the processed result to the output path, reporting housekeeping that failed after it.

    Args:
        video_file (VideoFile): The processed video file to write out.
        destination (Path): Where the result is written.
        overwrite (bool): Replace the destination without keeping a backup.
        keep_original (bool): Never remove the input file, because the user chose the
            destination with `--out`.

    Returns:
        tuple[OutputWrite | None, list[str]]: The write, or None when there was no result to
            write, and warnings about temp files or an original that could not be removed.
    """
    if video_file.temp_file.latest_temp_path() == video_file.path:
        return None, []

    write = copy_result_to_output(video_file, destination, overwrite=overwrite)
    warnings: list[str] = []

    # The write above already landed and swapped the result into place, so a failure past
    # this point is housekeeping, not a failed write: warn instead of discarding the
    # "Saved to" message and counting a successful file as failed. Each step is guarded
    # separately so a failed temp cleanup cannot skip the removal below, and so neither
    # failure is ever reported under the other one's description.
    try:
        video_file.temp_file.clean_up()
    except OSError as e:
        warnings.append(f"{TEMP_CLEANUP_WARNING}: {e}")

    # This removal exists for a container change that renamed the result (e.g. `.mkv` to
    # `.webm`), where `--overwrite` means "do not leave two copies of the same film".
    # `--out` writes to a destination the user chose, so the input is not a leftover copy
    # of the output and must survive.
    if overwrite and not keep_original and write.path != video_file.path:
        pp.debug(f"Delete: {video_file.path}")
        try:
            video_file.path.unlink()
        except OSError as e:
            warnings.append(f"{ORIGINAL_REMOVAL_WARNING} {video_file.path}: {e}")

    return write, warnings


def approve_upscale(video_file: VideoFile, *, source_width: int, dryrun: bool) -> bool:
    """Warn that `--1080p --force` would upscale a smaller video and ask the user to approve it.

    Upscaling adds no detail and makes the file larger, so the prompt defaults to no. A
    dry run only warns, since it changes nothing. A run that cannot prompt skips the
    upscale rather than guessing.

    Args:
        video_file (VideoFile): The file the plan would upscale.
        source_width (int): The source video's width in pixels.
        dryrun (bool): Whether this run only previews its changes.

    Returns:
        bool: True when the user approves the upscale.
    """
    pp.warning(
        f"`{video_file.name}` is {source_width}px wide. Converting it to 1080p upscales it "
        f"to {FHD_RESOLUTION.width}px, which adds no detail and makes the file larger."
    )
    if dryrun:
        return True

    approved = ask_if_interactive(
        lambda: Confirm.ask("Upscale this video to 1080p?", default=False, console=pp.console())
    )
    if approved is None:
        pp.warning("Cannot ask for approval in a non-interactive run. Skipping the upscale.")
        return False
    return approved


def _nearest_existing_directory(path: Path) -> Path:
    """Return `path` or its closest ancestor that exists, since `--out` can name a new folder."""
    return next(candidate for candidate in (path, *path.parents) if candidate.exists())


def check_free_space(video_file: VideoFile, *, temp_dir: Path, destination_dir: Path) -> None:
    """Stop a file before encoding when its disks cannot hold the result.

    A transcode can take hours, so fail fast rather than discover a full disk at the end.
    The encode writes the result to the temp directory, and the final copy stages another
    copy beside the destination. Each copy needs `OUTPUT_SIZE_MARGIN` times the source
    size. When both share a filesystem it must hold both.

    Args:
        video_file (VideoFile): The file about to be encoded.
        temp_dir (Path): The directory the encode writes into.
        destination_dir (Path): The directory the result is copied into.

    Raises:
        VideoCleanError: If a filesystem has less free space than the run needs on it.
    """
    output_size = math.ceil(video_file.path.stat().st_size * OUTPUT_SIZE_MARGIN)
    destination_dir = _nearest_existing_directory(destination_dir.expanduser().resolve())
    required_space: list[tuple[Path, int]]
    if temp_dir.stat().st_dev == destination_dir.stat().st_dev:
        required_space = [(temp_dir, 2 * output_size)]
    else:
        required_space = [(temp_dir, output_size), (destination_dir, output_size)]

    for directory, required in required_space:
        free = shutil.disk_usage(directory).free
        if free < required:
            raise VideoCleanError(
                path=video_file.path,
                reason=(
                    f"{NOT_ENOUGH_SPACE_ERROR} on {directory}: "
                    f"needs {decimal(required)}, has {decimal(free)}"
                ),
            )


def clean_video_file(
    video_file: VideoFile, *, destination_dir: Path, config: Config, options: RunOptions
) -> tuple[ConversionPlan, FfmpegCommand | None]:
    """Plan a file's cleaning, report the plan, and run it unless it changes nothing.

    Report the operations up front, before the ffmpeg progress bar starts.

    Args:
        video_file (VideoFile): The file to clean.
        destination_dir (Path): The directory the result is copied into, checked for
            free space before encoding.
        config (Config): The settings for this run.
        options (RunOptions): The CLI-only values for this run.

    Returns:
        tuple[ConversionPlan, FfmpegCommand | None]: The plan, whose `output_suffix` names
            any container change, and the ffmpeg command that ran or was previewed, or None
            when the plan changes nothing.
    """
    plan = video_file.plan_clean(config=config, options=options)

    if plan.upscale_source_width is not None and not approve_upscale(
        video_file, source_width=plan.upscale_source_width, dryrun=options.dryrun
    ):
        plan = video_file.plan_clean(config=config, options=replace(options, video_1080=False))
        plan.actions.append(
            PlanAction(label=SCALE_TO_1080P_LABEL, applied=False, reason=UPSCALE_DECLINED_REASON)
        )

    # Only a dry run may reach the encode with the question unanswered
    if not options.dryrun and INTERLACE_QUESTION_NOTICE in plan.notices:
        raise VideoCleanError(path=video_file.path, reason=UNDECIDED_INTERLACE_REASON)

    for note in plan.debug_notes:
        pp.debug(note)
    for notice in plan.notices:
        pp.info(notice)

    # A real run always follows these with write-outcome substeps, so leave the tree
    # open for them; a dry run writes nothing, making the operations its last lines.
    render_operations(plan.actions, debug=options.verbosity >= 1, closes_tree=options.dryrun)

    # Nothing to encode: the operations above already reported "No changes needed"
    command: FfmpegCommand | None = None
    if video_file.plan_changes_file(plan):
        command = video_file.prepare_ffmpeg(
            plan.build_command(),
            suffix=plan.output_suffix,
            step="clean",
            config=config,
            dryrun=options.dryrun,
        )
        if not options.dryrun:
            check_free_space(
                video_file=video_file,
                temp_dir=command.output_path.parent,
                destination_dir=destination_dir,
            )
        run_ffmpeg_step(video_file, command, title="Processing", dryrun=options.dryrun)
    return plan, command


def clean_one_file(
    video_file: VideoFile,
    *,
    out_path_override: Path | None,
    config: Config,
    options: RunOptions,
    probe_for_report: bool,
) -> CleanOutcome:
    """Clean one file and return what happened, so a batch can report it and move on.

    A file-level failure becomes a `FAILED` outcome instead of an exception, so one
    unusable file never discards the files queued behind it. An interrupt still
    propagates, after the steps this file finished are rendered.

    Args:
        video_file (VideoFile): The file to clean.
        out_path_override (Path | None): The `--out` destination, or None to write beside
            the input.
        config (Config): The settings for this run.
        options (RunOptions): The CLI-only values for this run.
        probe_for_report (bool): Describe the input file before planning and the written
            file after a successful write. Pass True only when a report needs the
            descriptions, so a plain run does no extra probing.

    Returns:
        CleanOutcome: The file's outcome.
    """
    outcome = CleanOutcome(video_file=video_file, status=CleanStatus.FAILED)
    try:
        # Read before ffmpeg runs: once a temp file exists, the probe reads that instead
        if probe_for_report:
            outcome.original = file_object(video_file)
        verifying = verification_enabled(config=config, options=options)
        source_length = SourceLength.of_video(video_file) if verifying else None
        destination = out_path_override or video_file.path
        plan, command = clean_video_file(
            video_file=video_file,
            destination_dir=destination.parent,
            config=config,
            options=options,
        )
        outcome.plan = plan
        if command is None:
            outcome.status = CleanStatus.UNCHANGED
            return outcome
        outcome.ffmpeg_command = command.arguments
        if plan.output_suffix:
            destination = destination.with_suffix(plan.output_suffix)
        if options.dryrun:
            outcome.status = CleanStatus.PLANNED
            outcome.destination = destination.expanduser().resolve()
            return outcome

        expected = expected_clean_output(plan, source_length=source_length) if verifying else None
        outcome.verify = run_verify_step(video_file, expected=expected, level=config.verify)
        outcome.write, outcome.warnings = write_output(
            video_file,
            destination=destination,
            overwrite=config.overwrite,
            keep_original=out_path_override is not None,
        )
        if outcome.write is None:
            outcome.status = CleanStatus.UNCHANGED
        else:
            outcome.status = CleanStatus.CLEANED
            outcome.destination = outcome.write.path
            if probe_for_report:
                record_written_file(outcome, path=outcome.write.path)
    # `cappa.Exit` is deliberately not caught: it carries KeyboardInterrupt, which means
    # stop the whole run.
    except (VideoCleanError, VideoProbeError, RuntimeError, OSError) as e:
        # VideoCleanError/VideoProbeError's str() already embeds the path, so use the
        # short reason instead to avoid naming the file twice in one line.
        outcome.reason = e.reason if isinstance(e, (VideoCleanError, VideoProbeError)) else str(e)
        # A failed file still leaves its full-size temp transcode on disk; clear it now
        # rather than letting a batch that fails on every file pile up N of them until
        # atexit finally clears them.
        try:
            video_file.temp_file.clean_up()
        except OSError as cleanup_error:
            pp.debug(f"Could not clean up temporary files for {video_file.path}: {cleanup_error}")
    except BaseException:
        # The run stops here, but the steps this file already finished still report.
        render_clean_outcome(outcome, dryrun=options.dryrun)
        raise
    return outcome


def record_written_file(outcome: CleanOutcome, *, path: Path) -> None:
    """Describe the written file on the outcome, since `--out` or a container change can move it.

    The file is already written, so a probe failure becomes a warning, not a failed file.

    Args:
        outcome (CleanOutcome): The outcome that receives the description or the warning.
        path (Path): The written file.
    """
    try:
        outcome.result = file_object(VideoFile(path))
    except (VideoProbeError, OSError) as e:
        outcome.warnings.append(f"{RESULT_PROBE_WARNING}: {e}")


def render_clean_outcome(outcome: CleanOutcome, *, dryrun: bool) -> None:
    """Print a file's outcome as the substep tree beneath its name.

    Args:
        outcome (CleanOutcome): The file's outcome, possibly unfinished when the run was
            interrupted.
        dryrun (bool): Whether the run only previewed its changes. A dry run's operation
            list already closed the tree, so an unchanged file adds nothing beneath it.
    """
    substeps: list[str] = []
    if outcome.verify is not None:
        substeps.append(verified_message(outcome.verify))
    if outcome.write is not None:
        substeps.extend(output_write_messages(outcome.write))
    if outcome.status == CleanStatus.UNCHANGED and not dryrun:
        substeps.append(f"No changes made: `{outcome.video_file.name}`")
    substeps.extend(f"{SYMBOL_CROSS} {warning}" for warning in outcome.warnings)
    if outcome.reason is not None:
        substeps.append(f"{SYMBOL_CROSS} Failed: {outcome.reason}")
    render_substeps(substeps)


def select_video_files(
    clean_cmd: CleanCommand, config: Config, options: RunOptions
) -> list[VideoFile]:
    """Resolve the files to clean from either explicit paths or a `--from` discovery run.

    Keep the two modes strictly separate so a query and a path list can never disagree
    about what the command is about to rewrite.

    Args:
        clean_cmd (CleanCommand): The parsed clean command.
        config (Config): The settings for this run.
        options (RunOptions): The CLI-only values for this run.

    Returns:
        list[VideoFile]: The files to process, in the order they should be processed.

    Raises:
        RunStopped: If the two modes are mixed, if discovery flags appear without
            `--from`, or if neither a file nor `--from` was given.
        cappa.Exit: If `--from` does not name an existing directory, holds no matching
            files, or the selection is not confirmed.
    """
    discovery = clean_cmd.discovery
    discovery_flags = discovery.flags_in_use() + (["`--yes`"] if clean_cmd.yes else [])

    if clean_cmd.from_ is not None and clean_cmd.files:
        raise stop_run(FROM_WITH_FILES_ERROR)

    if clean_cmd.from_ is None and discovery_flags:
        msg = f"{REQUIRES_FROM_ERROR}: {', '.join(discovery_flags)}"
        raise stop_run(msg)

    if clean_cmd.from_ is None:
        if not clean_cmd.files:
            raise stop_run(NO_FILES_OR_FROM_ERROR)
        return coerce_video_files(clean_cmd.files)

    with ProbeCache(config.cache_dir) as probe_cache:
        report = discover_from_directory(
            directory=clean_cmd.from_,
            options=discovery,
            argument_name="`--from`",
            probe_cache=probe_cache,
            limit=discovery.limit,
        )

    present_discovery(report)

    # A dry run previews rather than acts, so there is nothing to approve.
    if not options.dryrun:
        confirm_selection(report, assume_yes=clean_cmd.yes, overwrite=config.overwrite)

    # Discovery already probed every file and `VideoFile` caches its probe, so reusing
    # these instances skips a second ffprobe per file.
    return [result.video_file for result in report.results]


def main(
    clean_cmd: CleanCommand,
    vidcleaner: VidCleaner,
    config: Annotated[Config, Dep(build_config)],
    options: Annotated[RunOptions, Dep(build_run_options)],
) -> None:
    """Process video files according to specified cleaning options.

    Compose stream reordering, audio/subtitle filtering, downmixing, scaling, and codec
    conversion into a single ffmpeg pass per file. With `--json`, also print one
    document for the whole run to stdout.

    Args:
        clean_cmd (CleanCommand): Clean-specific command options
        vidcleaner (VidCleaner): The parsed CLI arguments, used to start the JSON report.
        config (Config): The settings for this run
        options (RunOptions): The CLI-only values for this run

    Raises:
        RunStopped: If incompatible options are specified, such as both H265 and VP9.
        cappa.Exit: If one or more files failed to process.
    """
    report = json_report_for(vidcleaner)
    summary = dict.fromkeys(("total", *(str(status) for status in CleanStatus)), 0)
    failures: list[str] = []
    skipped: list[dict[str, str]] = []

    with report_on_exit(report):
        if report:
            report.summary = summary
            report.extra["skipped"] = skipped

        if options.h265 and options.vp9:
            raise stop_run(H265_AND_VP9_ERROR)

        # Check the `--out`/`--from` conflict before discovery runs, so an empty `--from`
        # directory can't short-circuit the command with "no files found" ahead of this error.
        out_path_override = resolve_out_path_override(
            clean_cmd.files, out=options.out, from_directory=clean_cmd.from_
        )
        video_files = select_video_files(clean_cmd=clean_cmd, config=config, options=options)
        decision = resolve_interlaced_files(
            video_files, options=options, cache_dir=config.cache_dir, assume_yes=clean_cmd.yes
        )
        if decision.skipped:
            pp.info(
                f"Skipped {len(decision.skipped)} interlaced file(s):",
                # `Text` keeps a `:name:` in a filename from turning into an emoji
                details=[Text(str(video_file.path)) for video_file in decision.skipped],
            )
            skipped.extend(
                {"path": str(video_file.path), "reason": INTERLACED_FILES_SKIPPED_REASON}
                for video_file in decision.skipped
            )

        for video_file in decision.files:
            # Print the video name first so live progress bars and the up-front operation
            # tree render beneath it.
            pp.info(Text(f"⇨ {video_file.path.name}"))
            outcome = clean_one_file(
                video_file,
                out_path_override=out_path_override,
                config=config,
                options=decision.options_for(video_file),
                probe_for_report=report is not None,
            )
            render_clean_outcome(outcome, dryrun=options.dryrun)
            if outcome.status == CleanStatus.FAILED:
                failures.append(f"{video_file.path.name}: {outcome.reason}")
            if report:
                report.files.append(clean_outcome_entry(outcome))
                summary["total"] += 1
                summary[outcome.status] += 1

    if failures:
        pp.error(f"{len(failures)} {FILES_FAILED_ERROR}", details=failures, markup=False)
        raise cappa.Exit(code=1)

    raise cappa.Exit(code=0)
