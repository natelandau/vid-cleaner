"""Run one ffmpeg step for a subcommand and report it."""

from collections.abc import Generator
from contextlib import contextmanager, nullcontext

import cappa
from nclutils import pp
from rich.markdown import Markdown
from rich.progress import Progress
from rich.text import Text

from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import SYMBOL_CHECK, VerifyLevel
from vid_cleaner.utils import TEMP_CLEANUP_WARNING, FfmpegCommand, run_ffmpeg

from vid_cleaner.controllers.validation import ExpectedOutput, verify_output  # isort: skip
from vid_cleaner.models.video_file import VideoFile  # isort: skip

DRY_RUN_HEADER_SUFFIX = "(dry run)"
OUTPUT_VERIFIED_MESSAGE = "Output verified"
FFMPEG_INTERRUPTED_MESSAGE = "Interrupted while running ffmpeg. Exiting..."
VERIFY_INTERRUPTED_MESSAGE = "Interrupted while verifying the output. Exiting..."


@contextmanager
def _exit_on_interrupt(video_file: VideoFile, *, message: str) -> Generator[None]:
    """Turn Ctrl-C into a clean exit that removes the file's temporary files.

    Raises:
        cappa.Exit: If the user interrupts the wrapped block.
    """
    try:
        yield
    except KeyboardInterrupt as e:
        # A partial output and earlier steps' files would otherwise be orphaned
        try:
            video_file.temp_file.clean_up()
        except OSError as cleanup_error:
            # An OSError escaping here would replace the exit, and batch loops catch
            # OSError, so the run would continue past the user's Ctrl-C
            pp.warning(f"{TEMP_CLEANUP_WARNING}: {cleanup_error}")
        raise cappa.Exit(message, code=1) from e


def verification_enabled(config: Config, options: RunOptions) -> bool:
    """Report whether a run checks its output, so callers build an expectation only when needed.

    Args:
        config (Config): The resolved settings.
        options (RunOptions): The flag-only options for this run.

    Returns:
        bool: False on a dry run, which writes no output, or when verification is off.
    """
    return not options.dryrun and config.verify != VerifyLevel.OFF


def run_ffmpeg_step(
    video_file: VideoFile, command: FfmpegCommand, *, title: str, dryrun: bool
) -> list[str]:
    """Run one ffmpeg step on a file with a progress bar, or preview it on a dry run.

    Args:
        video_file (VideoFile): The file to process.
        command (FfmpegCommand): The step, from `VideoFile.prepare_ffmpeg`.
        title (str): Name of the step, shown on the progress bar and in the outcome.
        dryrun (bool): Print the command instead of running it.

    Returns:
        list[str]: Substep messages describing the outcome. Empty on a dry run, since the
            command is previewed instead of run.

    Raises:
        cappa.Exit: If the user interrupts ffmpeg, after removing the file's temporary files.
    """
    command_line = " ".join(command.arguments)
    # A plain Text skips markup parsing, which renders stream specifiers such as :v: as emoji
    pp.trace(Text(f"RUN FFMPEG:\n{command_line}"))

    if dryrun:
        pp.header(f"{title} {DRY_RUN_HEADER_SUFFIX}")
        pp.console().print(Markdown(f"```console\n{command_line}\n```"))
        return []

    with (
        _exit_on_interrupt(video_file, message=FFMPEG_INTERRUPTED_MESSAGE),
        Progress(transient=True) as progress,
    ):
        task = progress.add_task(f"{title}…", total=100)
        run_ffmpeg(
            command=command,
            on_progress=lambda complete: progress.update(task, completed=complete),
        )

    video_file.temp_file.created_temp_file(command.output_path)
    pp.trace(f"Created temp file: {command.output_path}")
    return [f"{SYMBOL_CHECK} {title}"]


def run_verify_step(
    video_file: VideoFile, *, expected: ExpectedOutput | None, level: VerifyLevel
) -> list[str]:
    """Check the newest temp file against what the run promised, before it replaces the original.

    Args:
        video_file (VideoFile): The file whose newest temp file is checked.
        expected (ExpectedOutput | None): The streams and length the output must have, or None
            when verification is disabled for this run.
        level (VerifyLevel): How thorough the check is.

    Returns:
        list[str]: The success substep, or an empty list when there is no expectation or no
            temp file to check.

    Raises:
        VideoCleanError: If the output fails a check.
        cappa.Exit: If the user interrupts the check, after removing the file's temporary files.
    """
    output_path = video_file.temp_file.latest_temp_path()
    if expected is None or output_path == video_file.path:
        return []

    with (
        _exit_on_interrupt(video_file, message=VERIFY_INTERRUPTED_MESSAGE),
        # Only the decode takes long enough to need a spinner. Its progress is not
        # measurable, so the task stays indeterminate.
        Progress(transient=True) if level == VerifyLevel.FULL else nullcontext() as progress,
    ):
        if progress is not None:
            progress.add_task("Verifying output…", total=None)
        verify_output(output_path, source_path=video_file.path, expected=expected, level=level)

    return [f"{SYMBOL_CHECK} {OUTPUT_VERIFIED_MESSAGE} ({level})"]
