"""Clean subcommand."""

from pathlib import Path
from typing import Annotated

import cappa
from cappa import Dep
from nclutils import pp

from vid_cleaner.cli.discovery_output import (
    confirm_selection,
    discover_from_directory,
    present_discovery,
)
from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import SYMBOL_CROSS, SortOrder
from vid_cleaner.exceptions import VideoCleanError, VideoProbeError
from vid_cleaner.utils import (
    coerce_video_files,
    copy_to_output,
    render_operations,
    render_substeps,
    resolve_out_path_override,
)
from vid_cleaner.vidcleaner import CleanCommand, build_config, build_run_options

from vid_cleaner.cli.ffmpeg_step import run_ffmpeg_step  # isort: skip
from vid_cleaner.models.conversion_plan import ConversionPlan  # isort: skip
from vid_cleaner.models.video_file import VideoFile  # isort: skip


def write_output(
    video_file: VideoFile,
    *,
    destination: Path,
    overwrite: bool,
    keep_original: bool,
) -> list[str]:
    """Copy the processed result to the output path and return the closing substep messages.

    Args:
        video_file (VideoFile): The processed video file to write out.
        destination (Path): Where the result is written.
        overwrite (bool): Replace the destination without keeping a backup.
        keep_original (bool): Never remove the input file, because the user chose the
            destination with `--out`.

    Returns:
        list[str]: Substep messages describing the backup and save, or a single note when nothing changed.
    """
    if video_file.temp_file.latest_temp_path() == video_file.path:
        return [f"No changes made: `{video_file.name}`"]

    out_file, messages = copy_to_output(
        video_file.temp_file.latest_temp_path(),
        destination,
        overwrite=overwrite,
    )

    # The write above already landed and swapped the result into place, so a failure past
    # this point is housekeeping, not a failed write: warn instead of discarding the
    # "Saved to" message and counting a successful file as failed. Each step is guarded
    # separately so a failed temp cleanup cannot skip the removal below, and so neither
    # failure is ever reported under the other one's description.
    try:
        video_file.temp_file.clean_up()
    except OSError as e:
        messages.append(f"{SYMBOL_CROSS} Warning: could not clean up temporary files: {e}")

    # This removal exists for a container change that renamed the result (e.g. `.mkv` to
    # `.webm`), where `--overwrite` means "do not leave two copies of the same film".
    # `--out` writes to a destination the user chose, so the input is not a leftover copy
    # of the output and must survive.
    if overwrite and not keep_original and out_file != video_file.path:
        pp.debug(f"Delete: {video_file.path}")
        try:
            video_file.path.unlink()
        except OSError as e:
            messages.append(
                f"{SYMBOL_CROSS} Warning: could not remove the original {video_file.path}: {e}"
            )

    return messages


def clean_video_file(
    video_file: VideoFile, *, config: Config, options: RunOptions
) -> ConversionPlan:
    """Plan a file's cleaning, report the plan, and run it unless it changes nothing.

    Report the operations up front, before the ffmpeg progress bar starts.

    Args:
        video_file (VideoFile): The file to clean.
        config (Config): The settings for this run.
        options (RunOptions): The CLI-only values for this run.

    Returns:
        ConversionPlan: The plan, whose `output_suffix` names any container change.
    """
    plan = video_file.plan_clean(config=config, options=options)

    for note in plan.debug_notes:
        pp.debug(note)
    for notice in plan.notices:
        pp.info(notice)

    # A real run always follows these with write-outcome substeps, so leave the tree
    # open for them; a dry run writes nothing, making the operations its last lines.
    render_operations(plan.actions, debug=options.verbosity >= 1, closes_tree=options.dryrun)

    # Nothing to encode: the operations above already reported "No changes needed"
    if video_file.plan_changes_file(plan):
        command = video_file.prepare_ffmpeg(
            plan.build_command(),
            suffix=plan.output_suffix,
            step="clean",
            config=config,
            dryrun=options.dryrun,
        )
        run_ffmpeg_step(video_file, command, title="Processing", dryrun=options.dryrun)
    return plan


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
        cappa.Exit: If the two modes are mixed, if discovery flags appear without
            `--from`, if neither a file nor `--from` was given, or if `--from` does not
            name an existing directory.
    """
    discovery = clean_cmd.discovery
    # Compare against defaults rather than asking cappa what the user typed; an explicit
    # `--sort=alpha` would have been a no-op anyway, so treating it as unset is harmless.
    uses_discovery_flags = bool(
        discovery.filters
        or discovery.limit is not None
        or discovery.depth
        or discovery.reverse
        or discovery.sort != SortOrder.ALPHA
        or clean_cmd.yes
    )

    if clean_cmd.from_ is not None and clean_cmd.files:
        pp.error("`--from` cannot be combined with explicit file paths")
        raise cappa.Exit(code=1)

    if clean_cmd.from_ is None and uses_discovery_flags:
        pp.error(
            "These options require `--from`: `--filters`, `--sort`, `--reverse`, `--depth`, `--limit`, `--yes`"
        )
        raise cappa.Exit(code=1)

    if clean_cmd.from_ is None:
        if not clean_cmd.files:
            pp.error("Provide file path(s) or `--from` to discover them")
            raise cappa.Exit(code=1)
        return coerce_video_files(clean_cmd.files)

    report = discover_from_directory(
        directory=clean_cmd.from_,
        options=discovery,
        argument_name="`--from`",
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
    config: Annotated[Config, Dep(build_config)],
    options: Annotated[RunOptions, Dep(build_run_options)],
) -> None:
    """Process video files according to specified cleaning options.

    Compose stream reordering, audio/subtitle filtering, downmixing, scaling, and codec
    conversion into a single ffmpeg pass per file.

    Args:
        clean_cmd (CleanCommand): Clean-specific command options
        config (Config): The settings for this run
        options (RunOptions): The CLI-only values for this run

    Raises:
        cappa.Exit: If incompatible options are specified (e.g., both H265 and VP9), or
            if one or more files failed to process.
    """
    if options.h265 and options.vp9:
        pp.error("Cannot convert to both H265 and VP9")
        raise cappa.Exit(code=1)

    # Check the `--out`/`--from` conflict before discovery runs, so an empty `--from`
    # directory can't short-circuit the command with "no files found" ahead of this error.
    out_path_override = resolve_out_path_override(
        clean_cmd.files, out=options.out, from_directory=clean_cmd.from_
    )
    video_files = select_video_files(clean_cmd=clean_cmd, config=config, options=options)

    failures: list[str] = []

    for video_file in video_files:
        # Print the video name first so live progress bars and clean()'s up-front operation
        # tree render beneath it. Only the write-output messages are collected here and
        # rendered in `finally`, so they still show if `write_output()` raises.
        pp.info(f"⇨ {video_file.path.name}")
        substeps: list[str] = []

        try:
            plan = clean_video_file(video_file, config=config, options=options)
            destination = out_path_override or video_file.path
            if plan.output_suffix:
                destination = destination.with_suffix(plan.output_suffix)
            if not options.dryrun:
                substeps.extend(
                    write_output(
                        video_file,
                        destination=destination,
                        overwrite=config.overwrite,
                        keep_original=out_path_override is not None,
                    )
                )
        # One unusable or failing file must not discard the files queued behind it.
        # `cappa.Exit` is deliberately not caught: it carries KeyboardInterrupt, which
        # means stop the whole run.
        except (VideoCleanError, VideoProbeError, RuntimeError, OSError) as e:
            # VideoCleanError/VideoProbeError's str() already embeds the path, so use the
            # short reason instead to avoid naming the file twice in one line.
            detail = e.reason if isinstance(e, (VideoCleanError, VideoProbeError)) else str(e)
            failures.append(f"{video_file.path.name}: {detail}")
            substeps.append(f"{SYMBOL_CROSS} Failed: {detail}")
            # A failed file still leaves its full-size temp transcode on disk; clear it now
            # rather than letting a batch that fails on every file pile up N of them until
            # atexit finally clears them.
            try:
                video_file.temp_file.clean_up()
            except OSError as cleanup_error:
                pp.debug(
                    f"Could not clean up temporary files for {video_file.path}: {cleanup_error}"
                )
        finally:
            render_substeps(substeps)

    if failures:
        pp.error(f"{len(failures)} file(s) failed", details=failures, markup=False)
        raise cappa.Exit(code=1)

    raise cappa.Exit(code=0)
