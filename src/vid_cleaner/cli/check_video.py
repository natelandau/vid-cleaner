"""Check subcommand."""

from collections.abc import Iterator
from pathlib import Path

import cappa
from nclutils import pp

from vid_cleaner.cli.discovery_output import (
    FROM_WITH_FILES_ERROR,
    NO_FILES_OR_FROM_ERROR,
    NO_VIDEO_FILES_FOUND,
    REQUIRES_FROM_ERROR,
    discover_from_directory,
)
from vid_cleaner.controllers.discovery import DiscoveryReport
from vid_cleaner.controllers.validation import CheckResult, check_probed_video, check_video_file
from vid_cleaner.utils import drop_macos_sidecars, stop_run, track_progress
from vid_cleaner.vidcleaner import CheckCommand, VidCleaner, build_probe_cache, json_report_for
from vid_cleaner.views.json_output import check_report_entry, report_on_exit


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
    try:
        cwd = Path.cwd()
    except FileNotFoundError:
        # A deleted working directory leaves nothing to shorten the path against.
        return str(path.expanduser())
    resolved = path.expanduser().resolve()

    if resolved.parent == cwd:
        return resolved.name

    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(resolved)


def check_discovered_files(report: DiscoveryReport, *, deep: bool) -> Iterator[CheckResult]:
    """Check every video file a `--from` discovery found, yielding each verdict as it is reached.

    Args:
        report (DiscoveryReport): The files discovery found under the `--from` directory.
        deep (bool): Decode every frame instead of trusting the header.

    Yields:
        CheckResult: A verdict per file found, unreadable files included.

    Raises:
        cappa.Exit: With code 0 when the directory holds no video files.
    """
    if report.total == 0 and not report.skipped:
        pp.warning(f"{NO_VIDEO_FILES_FOUND} in {report.root}")
        raise cappa.Exit(code=0)

    # Discovery reports the files it could not read separately from the ones it kept, and
    # an unreadable file has no traits, so `--filters` can never have excluded one. Report
    # them all: hiding a failure behind a filter would defeat the command.
    for error in report.skipped:
        yield CheckResult(path=error.path, ok=False, reason=error.reason)

    # Discovery already probed every file it kept and `VideoFile` caches its probe, so
    # finishing the verdict here costs no second ffprobe.
    for result in track_progress(
        report.results,
        description=f"Decoding {len(report.results)} files...",
        transient=True,
        disable=not deep,
    ):
        yield check_probed_video(result.video_file, deep=deep)


def check_explicit_files(check_cmd: CheckCommand) -> Iterator[CheckResult]:
    """Check each path the user named, in the order they named it, yielding each verdict.

    Args:
        check_cmd (CheckCommand): The parsed check command.

    Yields:
        CheckResult: A verdict per path given, except macOS `._` metadata files.

    Raises:
        cappa.Exit: If every path given is a macOS `._` metadata file.
    """
    files = drop_macos_sidecars(check_cmd.files)
    for path in track_progress(
        files,
        description=f"Decoding {len(files)} files...",
        transient=True,
        disable=not check_cmd.deep,
    ):
        yield check_video_file(path, deep=check_cmd.deep)


def select_results(check_cmd: CheckCommand) -> Iterator[CheckResult]:
    """Resolve and check the files named either explicitly or by a `--from` query.

    Keep the two modes strictly separate so a query and a path list can never disagree
    about what was checked. Verdicts arrive one at a time, so an interrupted run still
    has the ones it finished.

    Args:
        check_cmd (CheckCommand): The parsed check command.

    Returns:
        Iterator[CheckResult]: A verdict per file checked.

    Raises:
        cappa.Exit: If the two modes are mixed, if discovery flags appear without
            `--from`, or if neither a file nor `--from` was given.
    """
    discovery_flags = check_cmd.discovery.flags_in_use()

    if check_cmd.from_ is not None and check_cmd.files:
        raise stop_run(FROM_WITH_FILES_ERROR)

    if check_cmd.from_ is None and discovery_flags:
        msg = f"{REQUIRES_FROM_ERROR}: {', '.join(discovery_flags)}"
        raise stop_run(msg)

    if check_cmd.from_ is not None:
        with build_probe_cache() as probe_cache:
            report = discover_from_directory(
                directory=check_cmd.from_,
                options=check_cmd.discovery,
                argument_name="`--from`",
                probe_cache=probe_cache,
            )
        return check_discovered_files(report, deep=check_cmd.deep)

    if not check_cmd.files:
        raise stop_run(NO_FILES_OR_FROM_ERROR)

    return check_explicit_files(check_cmd)


def main(check_cmd: CheckCommand, vidcleaner: VidCleaner) -> None:
    """Report whether each named video file is valid, and exit non-zero if any is not.

    With `--json`, also print one document for the whole run to stdout.

    Args:
        check_cmd (CheckCommand): Check-specific command options
        vidcleaner (VidCleaner): The parsed CLI arguments, used to start the JSON report.

    Raises:
        cappa.Exit: With code 0 when every file checked is valid, code 1 when any is not.
    """
    report = json_report_for(vidcleaner)
    invalid = 0
    summary = {"total": 0, "valid": 0, "invalid": 0}

    with report_on_exit(report):
        if report:
            report.summary = summary
        results: list[CheckResult] = []
        for result in select_results(check_cmd):
            results.append(result)
            if report:
                report.files.append(check_report_entry(result))
                summary["total"] += 1
                summary["valid" if result.ok else "invalid"] += 1

        # `pp.success` and `pp.error` render their own markers and route failures to stderr,
        # so `check ... 2>/dev/null` narrows the output to the valid files. Both default to
        # `markup=False`, which is what keeps the square brackets in a release filename or an
        # ffmpeg diagnostic from being read as Rich markup tags and dropped.
        for result in results:
            name = display_path(result.path)
            if result.ok:
                pp.success(name)
                continue

            invalid += 1
            pp.error(f"{name}  {result.reason}")

        pp.info(
            f"\nChecked {len(results)} file(s): {len(results) - invalid} valid, {invalid} invalid"
        )

    raise cappa.Exit(code=1 if invalid else 0)
