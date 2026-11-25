"""Console output helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from nclutils import pp
from rich.progress import Progress, track
from rich.text import Text

from vid_cleaner.constants import SYMBOL_CHECK, SYMBOL_CROSS, TREE_BRANCH, TREE_LAST
from vid_cleaner.exceptions import RunStopped

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from vid_cleaner.models.conversion_plan import PlanAction

T = TypeVar("T")


def progress_display(*, transient: bool = True) -> Progress:
    """Create a progress display bound to the shared console.

    Use this instead of a bare `Progress` so the display follows `pp`'s console and
    stays off stdout when `--json` routes messages to stderr.

    Args:
        transient (bool): Remove the display from the terminal once it finishes.

    Returns:
        Progress: A progress display to use as a context manager.
    """
    return Progress(console=pp.console(), transient=transient)


def track_progress(
    sequence: Iterable[T],
    *,
    description: str,
    transient: bool = True,
    disable: bool = False,
    total: int | None = None,
) -> Iterator[T]:
    """Iterate a sequence while showing a progress bar on the shared console.

    Use this instead of rich's `track` so the bar follows `pp`'s console and stays off
    stdout when `--json` routes messages to stderr.

    Args:
        sequence (Iterable[T]): The items to iterate.
        description (str): The label shown beside the bar.
        transient (bool): Remove the bar from the terminal once it finishes.
        disable (bool): Show no bar.
        total (int | None): The number of items, for a sequence with no length such as
            an iterator. Defaults to `len(sequence)`.

    Yields:
        T: Each item of `sequence`.
    """
    yield from track(
        sequence,
        description=description,
        transient=transient,
        console=pp.console(),
        disable=disable,
        total=total,
    )


def _render_tree(lines: list[tuple[str, str]], *, closes_tree: bool) -> None:
    """Print styled lines beneath the current video as a faked ``pp`` sub-item tree.

    A real ``pp.step`` spinner recomputes the connector on each live refresh, but its
    live display cannot coexist with the live ffmpeg/copy progress bars, so the tree is
    faked here: the whole list is rendered at once so the final child closes with ``└─``.

    Args:
        lines: ``(message, style)`` pairs in display order; ``style`` is a Rich style
            applied to the message text (empty string for no styling).
        closes_tree: Whether these are the last lines for the current video. When False
            every line branches with ``├─``, so a later batch can close the same tree.
    """
    last_index = len(lines) - 1 if closes_tree else -1
    for index, (message, style) in enumerate(lines):
        connector = TREE_LAST if index == last_index else TREE_BRANCH
        # `sub.pipe` is nclutils' dim connector style, matching its own Step sub-items.
        line = Text.from_markup(f"  [sub.pipe]{connector}[/] ")
        line.append(message, style=style)
        pp.info(line)


def render_substeps(messages: list[str]) -> None:
    """Render operation outcomes as a tree of children styled like ``pp`` sub-items.

    Keep presentation in the CLI layer: the model returns these strings without emitting them, and the caller renders the whole list at once so the final child can close with ``└─``.

    Args:
        messages: Outcome lines to display beneath the current video, in order.
    """
    _render_tree([(message, "") for message in messages], closes_tree=True)


def render_operations(actions: list[PlanAction], *, debug: bool, closes_tree: bool = True) -> None:
    """Render the cleaning operations for a file as a tree beneath its name.

    Print the truthful operation list up front, before the ffmpeg progress bar. In
    normal mode only operations that actually run are shown; in debug mode every
    requested operation is shown, with skipped ones marked and annotated with a reason.
    When no operations are visible, render a single "No changes needed" line.

    Args:
        actions: The plan's operations, in display order.
        debug: When True, also show skipped operations with their reason.
        closes_tree: Whether the operations are all this video will print. Pass False when
            outcome substeps follow, so the file renders as one tree rather than two.
    """
    visible = actions if debug else [action for action in actions if action.applied]

    lines: list[tuple[str, str]] = []
    if not visible:
        lines.append(("No changes needed", ""))
    else:
        for action in visible:
            if action.applied:
                lines.append((f"{SYMBOL_CHECK} {action.label}", ""))
            else:
                suffix = f"  ({action.reason})" if action.reason else ""
                lines.append((f"{SYMBOL_CROSS} {action.label}{suffix}", "dim"))

    _render_tree(lines, closes_tree=closes_tree)


def stop_run(reason: str, *, details: list[str] | None = None) -> RunStopped:
    """Print why the run must stop and build the exception that stops it.

    Use this instead of printing and raising separately so the reason also reaches a JSON report.

    Args:
        reason: The explanation shown to the user and recorded in the report.
        details: Extra lines printed beneath the reason.

    Returns:
        RunStopped: The exception for the caller to raise.
    """
    pp.error(reason, details=details, markup=False)
    return RunStopped(reason)
