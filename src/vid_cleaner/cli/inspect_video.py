"""Inspect subcommand."""

import cappa
from nclutils import pp

from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import coerce_video_files
from vid_cleaner.vidcleaner import InspectCommand, VidCleaner, build_probe_cache, json_report_for
from vid_cleaner.views import stream_table
from vid_cleaner.views.json_output import file_object, interlace_scan_fields, report_on_exit

SCAN_LINE_TEMPLATE = "Scan: {verdict}"


def main(inspect_cmd: InspectCommand, vidcleaner: VidCleaner) -> None:
    """Print stream details for each video file so a user can see what a file holds.

    Continue past a file ffprobe cannot read, then exit 1 so scripts notice. With `--json`,
    print one document for the whole run.

    Args:
        inspect_cmd (InspectCommand): The inspect subcommand with its file list and flags.
        vidcleaner (VidCleaner): The parsed CLI arguments, used to start the JSON report.

    Raises:
        cappa.Exit: With code 1 when any file could not be read, otherwise 0.
    """
    report = json_report_for(vidcleaner)
    skipped: list[dict[str, str]] = []
    summary = {"total": 0, "skipped": 0}

    with report_on_exit(report):
        if report:
            report.summary = summary
            report.extra["skipped"] = skipped

        videos = coerce_video_files(inspect_cmd.files)
        summary["total"] = len(videos)
        with build_probe_cache() as probe_cache:
            for video in videos:
                try:
                    if report:
                        entry = file_object(video)
                        entry.update(interlace_scan_fields(video.interlace_scan(probe_cache)))
                        report.files.append(entry)
                    else:
                        pp.console().print(stream_table(video.probe_box))
                        scan = video.interlace_scan(probe_cache)
                        pp.info(SCAN_LINE_TEMPLATE.format(verdict=scan.description))
                except VideoProbeError as e:
                    pp.error(str(e))
                    skipped.append({"path": str(video.path), "reason": e.reason})
                # A file can vanish or lose its permissions after the path check
                except OSError as e:
                    pp.error(f"{video.path}: {e}")
                    skipped.append({"path": str(video.path), "reason": str(e)})
                summary["skipped"] = len(skipped)

    raise cappa.Exit(code=1 if skipped else 0)
