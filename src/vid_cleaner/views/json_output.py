"""Machine-readable JSON reports written to stdout."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import cappa

from vid_cleaner.constants import CleanStatus
from vid_cleaner.exceptions import RunStopped

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from vid_cleaner.cli.clean_video import CleanOutcome
    from vid_cleaner.constants import SortOrder, VideoTrait
    from vid_cleaner.controllers.discovery import DiscoveryReport
    from vid_cleaner.controllers.validation import CheckResult
    from vid_cleaner.models.video_file import VideoFile
    from vid_cleaner.utils import InterlaceScan

JSON_SCHEMA_VERSION = 1


@dataclass
class JsonReport:
    """Hold one command's JSON report so every command emits the same envelope."""

    command: str
    dry_run: bool = False
    summary: dict[str, int] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    interrupted: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Build the document in its fixed key order so consumers see a stable shape.

        Returns:
            dict[str, Any]: The envelope keys followed by the command-specific `extra` keys.
        """
        return {
            "schema_version": JSON_SCHEMA_VERSION,
            "command": self.command,
            "dry_run": self.dry_run,
            "interrupted": self.interrupted,
            "error": self.error,
            "summary": self.summary,
            "files": self.files,
            **self.extra,
        }


def file_object(video_file: VideoFile, *, size: int | None = None) -> dict[str, Any]:
    """Describe a video file for a report using ffprobe's raw streams and format.

    Args:
        video_file (VideoFile): The file to describe.
        size (int | None): The file's size in bytes when the caller already measured it,
            or None to read it from disk.

    Returns:
        dict[str, Any]: `path`, `name`, `size`, `streams`, and `format`.
    """
    ffprobe_output = video_file.ffprobe_output()
    return {
        "path": str(video_file.path),
        "name": video_file.path.name,
        "size": video_file.path.stat().st_size if size is None else size,
        "streams": ffprobe_output.get("streams", []),
        "format": ffprobe_output.get("format", {}),
    }


def interlace_scan_fields(scan: InterlaceScan | None) -> dict[str, bool | str | None]:
    """Serialize an interlace scan to the `interlaced` and `field_order` JSON fields.

    Call this so every command reports a scan in the same shape.

    Args:
        scan (InterlaceScan | None): The scan result, or None when no scan ran.

    Returns:
        dict[str, bool | str | None]: `interlaced` and `field_order`, each null when the
            scan did not run or could not decide.
    """
    return {
        "interlaced": scan.interlaced if scan else None,
        "field_order": scan.field_order.value if scan and scan.field_order else None,
    }


def check_report_entry(result: CheckResult) -> dict[str, Any]:
    """Convert one check verdict to its JSON file entry so scripts can read the file's result.

    Args:
        result (CheckResult): The verdict for one file.

    Returns:
        dict[str, Any]: The `path`, resolved like every other command's, plus `ok`,
            `reason` (null when the file is valid), `interlaced` and `field_order`. The
            last two are null for an invalid file or an undecided scan.
    """
    return {
        "path": str(result.path.expanduser().resolve()),
        "ok": result.ok,
        "reason": result.reason,
        **interlace_scan_fields(result.scan),
    }


def search_query(
    *,
    root: Path,
    filters: set[VideoTrait],
    sort: SortOrder,
    descending: bool,
    depth: int,
    limit: int | None,
) -> dict[str, Any]:
    """Describe a search query so a script can see what produced the list.

    Args:
        root (Path): The directory searched.
        filters (set[VideoTrait]): The traits a file must have all of.
        sort (SortOrder): The key results are ranked by.
        descending (bool): Whether the largest or last value comes first.
        depth (int): Subdirectory levels searched.
        limit (int | None): The `--limit` the query used.

    Returns:
        dict[str, Any]: The `query` object of the `search` document.
    """
    return {
        "root": str(root),
        "filters": sorted(str(trait) for trait in filters),
        "sort": str(sort),
        "descending": descending,
        "depth": depth,
        "limit": limit,
    }


def search_report_fields(
    report: DiscoveryReport,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Convert a discovery report to the parts of the `search` document.

    Args:
        report (DiscoveryReport): The selection discovery produced.

    Returns:
        tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]: The file entries, the
            summary counts, and the command-specific `skipped` key.
    """
    files = [
        {
            # Discovery already measured the file, and a second stat could fail on a file
            # removed since, which would end the whole report
            **file_object(result.video_file, size=result.size),
            "traits": [str(trait) for trait in result.traits],
            "matches": [str(trait) for trait in result.matches],
            "bitrate": result.bitrate,
        }
        for result in report.results
    ]
    summary = {
        "total": report.total,
        "shown": len(report.results),
        "skipped": len(report.skipped),
        "truncated": report.truncated,
    }
    extra = {
        "skipped": [{"path": str(error.path), "reason": error.reason} for error in report.skipped],
    }
    return files, summary, extra


def clean_outcome_entry(outcome: CleanOutcome) -> dict[str, Any]:
    """Convert one file's clean outcome to its JSON entry so scripts see what happened to it.

    Args:
        outcome (CleanOutcome): The finished outcome of one file.

    Returns:
        dict[str, Any]: The input `path` and the outcome's `status`, `reason`, `original`, `result`,
            `destination`, `planned_streams`, `backup_path`, `actions`, `notices`,
            `ffmpeg_command`, `verify`, and `warnings`.
    """
    plan = outcome.plan
    write = outcome.write
    planned_streams = None
    if plan is not None and outcome.status == CleanStatus.PLANNED:
        planned_streams = [
            {
                "source_index": stream.source_index,
                "codec_type": str(stream.codec_type),
                "encoder": stream.codec,
                "filter": stream.stream_filter,
                "metadata": stream.metadata,
            }
            for stream in plan.streams
        ]

    return {
        "path": str(outcome.video_file.path),
        "status": str(outcome.status),
        "reason": outcome.reason,
        "original": outcome.original,
        "result": outcome.result,
        "destination": str(outcome.destination) if outcome.destination else None,
        "planned_streams": planned_streams,
        "backup_path": str(write.backup_path) if write and write.backup_path else None,
        "actions": [
            {"label": action.label, "applied": action.applied, "reason": action.reason}
            for action in (plan.actions if plan else [])
        ],
        "notices": list(plan.notices) if plan else [],
        "ffmpeg_command": outcome.ffmpeg_command,
        "verify": str(outcome.verify) if outcome.verify is not None else None,
        "warnings": list(outcome.warnings),
    }


def write_report(report: JsonReport) -> None:
    """Write the report to stdout as one JSON document.

    Bypass the Rich console so brackets and non-ASCII characters in file names stay intact.
    A reader that closed the pipe early, such as `| head`, does not change the exit code.

    Args:
        report (JsonReport): The report to write.
    """
    try:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # Point stdout at devnull so the interpreter's final flush cannot raise again and
        # replace the command's own exit with a traceback or exit code 120.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


@contextmanager
def report_on_exit(report: JsonReport | None) -> Generator[None]:
    """Write the report exactly once however the run ends, then let the exit continue.

    Do nothing when `report` is None, so callers can wrap a run unconditionally.

    Args:
        report (JsonReport | None): The report to complete and write, or None for text output.

    Yields:
        None: Control to the wrapped run.
    """
    if report is None:
        yield
        return

    try:
        yield
    except KeyboardInterrupt:
        report.interrupted = True
        raise
    except cappa.Exit as exc:
        if isinstance(exc.__cause__, KeyboardInterrupt):
            report.interrupted = True
        elif exc.code and not report.files:
            if isinstance(exc, RunStopped):
                report.error = exc.reason
            elif isinstance(exc.message, str):
                report.error = exc.message
        raise
    # A crash still ends the run, so the document must not read as a clean finish
    except Exception as exc:
        report.error = str(exc) or type(exc).__name__
        raise
    finally:
        write_report(report)
