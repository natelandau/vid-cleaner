"""Run one ffmpeg step for a subcommand and report it."""

from nclutils import pp
from rich.markdown import Markdown
from rich.progress import Progress

from vid_cleaner.constants import SYMBOL_CHECK

from vid_cleaner.models.video_file import FfmpegCommand, VideoFile  # isort: skip


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
    """
    command_line = " ".join(command.arguments)
    pp.trace(f"RUN FFMPEG:\n{command_line}")

    if dryrun:
        pp.header(f"{title} (dry run)")
        pp.console().print(Markdown(f"```console\n{command_line}\n```"))
        return []

    with Progress(transient=True) as progress:
        task = progress.add_task(f"{title}…", total=100)
        video_file.run_ffmpeg(
            command, on_progress=lambda complete: progress.update(task, completed=complete)
        )

    pp.trace(f"Created temp file: {command.output_path}")
    return [f"{SYMBOL_CHECK} {title}"]
