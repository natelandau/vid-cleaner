"""Check subcommand."""

from pathlib import Path

import cappa
from nclutils import pp
from rich.progress import track

from vid_cleaner import settings
from vid_cleaner.constants import SortOrder
from vid_cleaner.controllers.discovery import discover_video_files
from vid_cleaner.controllers.validation import CheckResult, check_probed_video, check_video_file
from vid_cleaner.vidcleaner import CheckCommand


def display_path(path: Path) -> str:
    """Render a checked path the shortest way that still locates the file.

    Print a bare filename for a file in the working directory, so a scan of a single
    directory reads as a list of names rather than a column of repeated prefixes, and
    fall back to a path as soon as the name alone would be ambiguous.

    Args:
        path (Path): The path as the user named it or as discovery found it.

    Returns:
        str: The path to print.
    """
    cwd = Path.cwd()
    resolved = path.expanduser().resolve()

    if resolved.parent == cwd:
        return resolved.name

    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(resolved)


def check_from_directory(check_cmd: CheckCommand) -> list[CheckResult]:
    """Check every video file under the `--from` directory.

    Args:
        check_cmd (CheckCommand): The parsed check command.

    Returns:
        list[CheckResult]: A verdict per file found, unreadable files included.

    Raises:
        cappa.Exit: If `--from` does not name an existing directory.
    """
    if not check_cmd.from_.is_dir():
        pp.error(f"`--from` must be an existing directory: {check_cmd.from_}")
        raise cappa.Exit(code=1)

    discovery = check_cmd.discovery
    report = discover_video_files(
        check_cmd.from_,
        depth=discovery.depth,
        filters=set(settings.filters),
        sort=discovery.sort,
        reverse=discovery.reverse,
    )

    if report.total == 0:
        pp.warning(f"No video files found in {check_cmd.from_}")
        raise cappa.Exit(code=0)

    # Discovery reports the files it could not read separately from the ones it kept, and
    # an unreadable file has no traits, so `--filters` can never have excluded one. Report
    # them all: hiding a failure behind a filter would defeat the command.
    failures = [
        CheckResult(path=error.path, ok=False, reason=error.reason) for error in report.skipped
    ]

    # Discovery already probed every file it kept and `VideoFile` caches its probe, so
    # finishing the verdict here costs no second ffprobe.
    passes = [
        check_probed_video(result.video_file, deep=check_cmd.deep)
        for result in track(
            report.results,
            description=f"Decoding {len(report.results)} files...",
            transient=True,
            disable=not check_cmd.deep,
        )
    ]

    return failures + passes


def check_explicit_files(check_cmd: CheckCommand) -> list[CheckResult]:
    """Check each path the user named, in the order they named it.

    Args:
        check_cmd (CheckCommand): The parsed check command.

    Returns:
        list[CheckResult]: A verdict per path given.
    """
    return [
        check_video_file(path, deep=check_cmd.deep)
        for path in track(
            check_cmd.files,
            description=f"Decoding {len(check_cmd.files)} files...",
            transient=True,
            disable=not check_cmd.deep,
        )
    ]


def select_results(check_cmd: CheckCommand) -> list[CheckResult]:
    """Resolve and check the files named either explicitly or by a `--from` query.

    Keep the two modes strictly separate so a query and a path list can never disagree
    about what was checked.

    Args:
        check_cmd (CheckCommand): The parsed check command.

    Returns:
        list[CheckResult]: A verdict per file checked.

    Raises:
        cappa.Exit: If the two modes are mixed, if discovery flags appear without
            `--from`, or if neither a file nor `--from` was given.
    """
    discovery = check_cmd.discovery
    # Compare against defaults rather than asking cappa what the user typed; an explicit
    # `--sort=alpha` would have been a no-op anyway, so treating it as unset is harmless.
    uses_discovery_flags = bool(
        discovery.filters
        or discovery.depth
        or discovery.reverse
        or discovery.sort != SortOrder.ALPHA
    )

    if check_cmd.from_ is not None and check_cmd.files:
        pp.error("`--from` cannot be combined with explicit file paths")
        raise cappa.Exit(code=1)

    if check_cmd.from_ is None and uses_discovery_flags:
        pp.error("These options require `--from`: `--filters`, `--sort`, `--reverse`, `--depth`")
        raise cappa.Exit(code=1)

    if check_cmd.from_ is not None:
        return check_from_directory(check_cmd)

    if not check_cmd.files:
        pp.error("Provide file path(s) or `--from` to discover them")
        raise cappa.Exit(code=1)

    return check_explicit_files(check_cmd)


def main(check_cmd: CheckCommand) -> None:
    """Report whether each named video file is valid, and exit non-zero if any is not.

    Args:
        check_cmd (CheckCommand): Check-specific command options

    Raises:
        cappa.Exit: With code 0 when every file checked is valid, code 1 when any is not.
    """
    results = select_results(check_cmd)

    # `pp.success` and `pp.error` render their own markers and route failures to stderr,
    # so `check ... 2>/dev/null` narrows the output to the valid files. Both default to
    # `markup=False`, which is what keeps the square brackets in a release filename or an
    # ffmpeg diagnostic from being read as Rich markup tags and dropped.
    invalid = 0
    for result in results:
        name = display_path(result.path)
        if result.ok:
            pp.success(name)
            continue

        invalid += 1
        pp.error(f"{name}  {result.reason}")

    pp.info(f"\nChecked {len(results)} file(s): {len(results) - invalid} valid, {invalid} invalid")

    raise cappa.Exit(code=1 if invalid else 0)
