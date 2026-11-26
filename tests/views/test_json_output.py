"""Tests for the shared JSON report."""

from typing import BinaryIO

import cappa
import pytest

from tests.helpers import load_ffprobe_json, read_json_document, run_vidcleaner
from vid_cleaner.constants import SortOrder, VideoTrait
from vid_cleaner.controllers.discovery import DiscoveryReport
from vid_cleaner.exceptions import RunStopped, VideoProbeError
from vid_cleaner.models import SearchResult
from vid_cleaner.views.json_output import (
    JsonReport,
    file_object,
    report_on_exit,
    search_report_fields,
    write_report,
)


def test_file_object_carries_unchanged_ffprobe_data(make_video) -> None:
    """Verify a file object reports ffprobe's raw streams and format."""
    # Given: A video probed from a recorded response
    video = make_video("reference.json")

    # When: Building its file object
    result = file_object(video)

    # Then: Streams and format match ffprobe's output exactly
    expected = load_ffprobe_json("reference.json")
    assert result["streams"] == expected["streams"]
    assert result["format"] == expected["format"]
    assert result["path"] == str(video.path)
    assert result["name"] == video.path.name
    assert result["size"] == video.path.stat().st_size


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(cappa.Exit(code=0), {"interrupted": False, "error": None}, id="clean_exit"),
        pytest.param(
            RunStopped("`--from` must be an existing directory"),
            {"interrupted": False, "error": "`--from` must be an existing directory"},
            id="run_stopped",
        ),
        pytest.param(
            cappa.Exit("File 'x.mkv' does not exist", code=1),
            {"interrupted": False, "error": "File 'x.mkv' does not exist"},
            id="exit_message",
        ),
        pytest.param(KeyboardInterrupt(), {"interrupted": True, "error": None}, id="interrupt"),
        pytest.param(
            OSError("disk gone"), {"interrupted": False, "error": "disk gone"}, id="crash"
        ),
    ],
)
def test_report_on_exit_writes_one_document(capsys, raised, expected) -> None:
    """Verify one JSON document is written and the exit is re-raised."""
    report = JsonReport(command="clean")

    with pytest.raises(type(raised)), report_on_exit(report):
        raise raised

    document = read_json_document(capsys)
    assert document["schema_version"] == 1
    assert {key: document[key] for key in expected} == expected


def test_report_on_exit_treats_exit_caused_by_interrupt_as_interrupted(capsys) -> None:
    """Verify an Exit chained from KeyboardInterrupt marks the report interrupted."""
    report = JsonReport(command="clean")

    with pytest.raises(cappa.Exit), report_on_exit(report):
        raise cappa.Exit(code=130) from KeyboardInterrupt()

    document = read_json_document(capsys)
    assert document["interrupted"] is True
    assert document["error"] is None


def test_report_on_exit_leaves_error_null_when_files_were_processed(capsys) -> None:
    """Verify a failing batch reports per-file results, not a run-level error."""
    report = JsonReport(command="clean", files=[{"path": "a.mkv"}])

    with pytest.raises(cappa.Exit), report_on_exit(report):
        raise cappa.Exit(code=1)

    assert read_json_document(capsys)["error"] is None


def test_search_report_fields_describes_a_limited_partly_unreadable_search(
    tmp_path, make_video
) -> None:
    """Verify the search document counts what the limit removed and lists unreadable files."""
    # Given: A limited search that kept one file, dropped one, and could not read one
    video = make_video("reference.json")
    unreadable = tmp_path / "bad.mkv"
    report = DiscoveryReport(
        root=tmp_path,
        results=[
            SearchResult(
                video_file=video,
                traits=[VideoTrait.H264],
                matches=[VideoTrait.H264],
                size=100,
                bitrate=1_000_000,
            )
        ],
        total=3,
        skipped=[VideoProbeError(path=unreadable, reason="Invalid data")],
        truncated=1,
        sort=SortOrder.SIZE,
        descending=True,
        filters={VideoTrait.H264},
        depth=2,
    )

    # When: Converting it to the search document's parts
    files, summary, extra = search_report_fields(report)

    # Then: The counts and the skipped entry reflect the search
    assert [entry["path"] for entry in files] == [str(video.path)]
    assert files[0]["matches"] == ["h264"]
    assert files[0]["size"] == 100
    assert summary == {"total": 3, "shown": 1, "skipped": 1, "truncated": 1}
    assert extra["skipped"] == [{"path": str(unreadable), "reason": "Invalid data"}]


def test_report_on_exit_without_report_writes_nothing(capsys) -> None:
    """Verify text-mode runs pass through untouched."""
    with report_on_exit(None):
        pass

    assert capsys.readouterr().out == ""


def test_write_report_keeps_brackets_and_non_ascii(capsys) -> None:
    """Verify file names survive verbatim, not escaped or markup-stripped."""
    name = "Movie [x265] 千と千尋.mkv"

    write_report(JsonReport(command="search", files=[{"name": name}]))

    assert name in capsys.readouterr().out


class _ClosedPipe:
    """Stand in for a stdout whose reader has gone away, such as `| head`."""

    def __init__(self, file: BinaryIO) -> None:
        self._file = file

    def write(self, text: str) -> int:  # noqa: ARG002
        raise BrokenPipeError

    def flush(self) -> None:
        raise BrokenPipeError

    def fileno(self) -> int:
        return self._file.fileno()


def test_report_on_exit_keeps_exit_code_when_reader_closes_pipe(monkeypatch, tmp_path) -> None:
    """Verify a closed stdout pipe does not replace the command's own exit."""
    with (tmp_path / "stdout").open("wb") as stdout_file:
        # Given: A stdout whose reader has closed the pipe
        monkeypatch.setattr("sys.stdout", _ClosedPipe(stdout_file))

        # When: A run ends with exit code 3 under a report
        with pytest.raises(cappa.Exit) as exc_info, report_on_exit(JsonReport(command="search")):
            raise cappa.Exit(code=3)

    # Then: The original exit propagates
    assert exc_info.value.code == 3


ENVELOPE_KEYS = ["schema_version", "command", "dry_run", "interrupted", "error", "summary", "files"]


@pytest.mark.parametrize(
    ("argv", "extra_keys", "summary_keys"),
    [
        (["inspect", "--json", "missing.mkv"], ["skipped"], ["total", "skipped"]),
        (
            ["search", "--json", "missing-directory"],
            ["query", "skipped"],
            ["total", "shown", "skipped", "truncated"],
        ),
        (
            ["check", "--json", "--from", "missing-directory"],
            [],
            ["total", "valid", "invalid", "interlaced"],
        ),
        (
            ["clean", "--json", "--h265", "--vp9", "movie.mkv"],
            ["skipped"],
            ["total", "cleaned", "unchanged", "failed", "planned"],
        ),
    ],
    ids=["inspect", "search", "check", "clean"],
)
def test_early_stop_keeps_the_success_envelope(
    capsys, monkeypatch, tmp_path, argv, extra_keys, summary_keys
) -> None:
    """Verify a run that stops before any file has the same keys as one that succeeds."""
    # Given: Arguments that stop the run before any file is processed
    monkeypatch.chdir(tmp_path)

    # When: Running the command with --json
    exit_code = run_vidcleaner(argv)

    # Then: The document has the success path's keys and summary keys, plus the error
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert doc["error"]
    assert list(doc) == ENVELOPE_KEYS + extra_keys
    assert list(doc["summary"]) == summary_keys
