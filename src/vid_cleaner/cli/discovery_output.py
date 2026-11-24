"""Shared presentation of discovery results for `search` and `clean`.

Keep this the only caller of `search_table` so the selection `clean` previews is the
same rendering `search` prints, by construction rather than by convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cappa
from nclutils import pp
from rich.filesize import decimal
from rich.prompt import Confirm

from vid_cleaner import settings
from vid_cleaner.views import search_table

if TYPE_CHECKING:
    from pathlib import Path

    from vid_cleaner.controllers.discovery import DiscoveryReport

NOT_AN_EXISTING_DIRECTORY_ERROR = "must be an existing directory"


def resolve_discovery_root(path: Path, *, argument_name: str) -> Path:
    """Normalize a user-supplied discovery directory, or stop the run if it is not one.

    Call this before discovery so the directory searched, the directory shown, and the
    directory relative paths are computed from are the same absolute path. A missing
    directory would otherwise discover nothing and exit 0, so a typo or an unmounted
    media share would look like an empty library.

    Args:
        path (Path): The directory as the user typed it. A `~` the shell left unexpanded,
            such as in a quoted path, is expanded here.
        argument_name (str): How the error message names the argument, e.g. "`--from`".

    Returns:
        Path: The absolute, symlink-resolved directory.

    Raises:
        cappa.Exit: With code 1 when `path` is not an existing directory.
    """
    try:
        resolved = path.expanduser().resolve()
        is_directory = resolved.is_dir()
    # `~name` for an unknown user raises RuntimeError, and before Python 3.14 `is_dir()`
    # raises PermissionError for a path inside a directory the user cannot search.
    except (RuntimeError, OSError) as e:
        pp.error(f"{argument_name} {NOT_AN_EXISTING_DIRECTORY_ERROR}: {path} ({e})")
        raise cappa.Exit(code=1) from e

    if not is_directory:
        pp.error(f"{argument_name} {NOT_AN_EXISTING_DIRECTORY_ERROR}: {path}")
        raise cappa.Exit(code=1)
    return resolved


def present_discovery(report: DiscoveryReport, *, root: Path) -> None:
    """Render a discovery report, or exit with the message its emptiness calls for.

    Distinguish "the directory holds no video files" (not an error) from "files were
    found but none matched" (an error), so a user can tell a bad path from a bad filter.
    Read the query off the report rather than taking it as arguments, so the no-match
    error can never name a filter set discovery did not actually apply.

    Args:
        report (DiscoveryReport): The selection to present, carrying the query that
            produced it.
        root (Path): The directory that was searched, as returned by
            `resolve_discovery_root`. Named in the empty-directory warning, and the base
            a recursive table lays out paths from.

    Raises:
        cappa.Exit: With code 0 when no video files exist under `root`, or code 1 when
            files were found but none survived the filters.
    """
    if report.total == 0:
        pp.warning(f"No video files found in {root}")
        raise cappa.Exit(code=0)

    if report.skipped:
        pp.debug(
            "Unreadable files",
            details=[f"{e.path}: {e.reason}" for e in report.skipped],
            markup=False,
        )

    if not report.results:
        # The table caption is the sole reporter of the skipped count on the success
        # path, but it never renders here, so fold the count into the error instead of
        # leaving the user unable to tell "nothing matches" from "nothing was readable".
        human_readable_filters = " and ".join(f"'{f}'" for f in sorted(report.filters))
        message = (
            f"No video files found matching {human_readable_filters}"
            if human_readable_filters
            else "No video files could be read"
        )
        if report.skipped:
            message += f" ({len(report.skipped)} file(s) skipped as unreadable)"
        pp.error(message)
        raise cappa.Exit(code=1)

    pp.console().print(search_table(report, root=root if report.recursive else None))


def _no_prompt_available() -> cappa.Exit:
    """Report that the run cannot be confirmed and build the exit to raise.

    Returns:
        cappa.Exit: The failure to raise at the call site.
    """
    pp.error("Refusing to run without a confirmation prompt. Pass `--yes` to proceed.")
    return cappa.Exit(code=1)


def confirm_selection(report: DiscoveryReport, *, assume_yes: bool) -> None:
    """Ask the user to approve a discovered selection before acting on it.

    Guard the expensive, file-rewriting half of a discovery run, and refuse to act on a
    non-interactive terminal rather than blocking forever on a prompt nobody can answer.

    Args:
        report (DiscoveryReport): The selection awaiting approval.
        assume_yes (bool): Skip the prompt and proceed.

    Raises:
        cappa.Exit: With code 1 when the terminal is not interactive and `assume_yes`
            is False, or code 0 when the user declines.
    """
    if assume_yes:
        return

    if not pp.console().is_terminal:
        raise _no_prompt_available()

    total_size = decimal(sum(result.size for result in report.results))
    # This prompt is the only gate on a whole library, and `--overwrite` leaves no backup
    # to restore from, so it has to say which of the two it is about to do.
    recovery = (
        "in place with no backup"
        if settings.overwrite
        else "keeping a timestamped backup of each original"
    )
    prompt = f"Clean these {len(report.results)} files ({total_size}) {recovery}?"
    try:
        approved = Confirm.ask(prompt, default=False, console=pp.console())
    # `is_terminal` describes the output stream, so a run with a terminal on stdout but a
    # closed or redirected stdin reaches the prompt and gets EOF instead of an answer.
    except EOFError as e:
        raise _no_prompt_available() from e

    if not approved:
        pp.info("Nothing to do")
        raise cappa.Exit(code=0)
