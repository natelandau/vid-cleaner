"""Console output helpers."""

from nclutils import pp
from rich.text import Text

from vid_cleaner.constants import TREE_BRANCH, TREE_LAST


def render_substeps(messages: list[str]) -> None:
    """Render operation outcomes as a tree of children styled like ``pp`` sub-items.

    Keep presentation in the CLI layer: the model returns these strings without emitting them, and the caller renders the whole list at once so the final child can close with ``└─``. A real ``pp.step`` spinner recomputes that connector on each live refresh, but its live display cannot coexist with the live ffmpeg/copy progress bars, so the tree is faked here instead.

    Args:
        messages: Outcome lines to display beneath the current video, in order.
    """
    last_index = len(messages) - 1
    for index, message in enumerate(messages):
        connector = TREE_LAST if index == last_index else TREE_BRANCH
        # `sub.pipe` is nclutils' dim connector style, matching its own Step sub-items.
        line = Text.from_markup(f"  [sub.pipe]{connector}[/] ")
        line.append(message)
        pp.info(line)
