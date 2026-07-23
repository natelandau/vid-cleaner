"""Console output helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nclutils import pp
from rich.text import Text

from vid_cleaner.constants import SYMBOL_CHECK, SYMBOL_CROSS, TREE_BRANCH, TREE_LAST

if TYPE_CHECKING:
    from vid_cleaner.models.conversion_plan import PlanAction


def _render_tree(lines: list[tuple[str, str]]) -> None:
    """Print styled lines beneath the current video as a faked ``pp`` sub-item tree.

    A real ``pp.step`` spinner recomputes the connector on each live refresh, but its
    live display cannot coexist with the live ffmpeg/copy progress bars, so the tree is
    faked here: the whole list is rendered at once so the final child closes with ``└─``.

    Args:
        lines: ``(message, style)`` pairs in display order; ``style`` is a Rich style
            applied to the message text (empty string for no styling).
    """
    last_index = len(lines) - 1
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
    _render_tree([(message, "") for message in messages])


def render_operations(actions: list[PlanAction], *, debug: bool) -> None:
    """Render the cleaning operations for a file as a tree beneath its name.

    Print the truthful operation list up front, before the ffmpeg progress bar. In
    normal mode only operations that actually run are shown; in debug mode every
    requested operation is shown, with skipped ones marked and annotated with a reason.
    When no operations are visible, render a single "No changes needed" line.

    Args:
        actions: The plan's operations, in display order.
        debug: When True, also show skipped operations with their reason.
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

    _render_tree(lines)
