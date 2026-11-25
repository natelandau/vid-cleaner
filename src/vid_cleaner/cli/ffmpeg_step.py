"""Run one ffmpeg step for a subcommand and report it."""

import cappa
from nclutils import pp
from rich.markdown import Markdown
from rich.progress import Progress
from rich.text import Text

from vid_cleaner.constants import SYMBOL_CHECK
from vid_cleaner.utils import FfmpegCommand, run_ffmpeg

from vid_cleaner.models.video_file import VideoFile  # isort: skip

DRY_RUN_HEADER_SUFFIX = "(dry run)"


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

    try:
        with Progress(transient=True) as progress:
            task = progress.add_task(f"{title}…", total=100)
            run_ffmpeg(
                command=command,
                on_progress=lambda complete: progress.update(task, completed=complete),
            )
    except KeyboardInterrupt as e:
        # A partial output and earlier steps' files would otherwise be orphaned
        video_file.temp_file.clean_up()
        message = "Interrupted while running ffmpeg. Exiting..."
        raise cappa.Exit(message, code=1) from e

    video_file.temp_file.created_temp_file(command.output_path)
    pp.trace(f"Created temp file: {command.output_path}")
    return [f"{SYMBOL_CHECK} {title}"]
