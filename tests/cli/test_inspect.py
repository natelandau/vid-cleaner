"""Test the vidcleaner inspect subcommand."""

import json
import re

import pytest

from tests.helpers import load_ffprobe_json, read_json_document, run_vidcleaner
from vid_cleaner.cli import inspect_video
from vid_cleaner.cli.inspect_video import SCAN_LINE_TEMPLATE, scan_verdict
from vid_cleaner.utils import FieldOrder, InterlaceScan


@pytest.mark.parametrize(
    "scan",
    [
        InterlaceScan(interlaced=True, field_order=FieldOrder.TFF),
        InterlaceScan(interlaced=False, field_order=None),
        InterlaceScan(interlaced=None, field_order=None),
    ],
    ids=["interlaced", "progressive", "undecided"],
)
def test_inspect_table(capsys, video_path, mock_video_probe, mock_interlace_detection, scan):
    """Verify inspect command displays video information and the interlace scan verdict."""
    # Given: Mock video file and ffprobe data
    mock_interlace_detection.return_value = scan
    args = ["inspect", str(video_path)]
    mock_video_probe("reference.json")

    # When: Running inspect command
    exit_code = run_vidcleaner(args)

    # Then: Output contains expected table data
    output = capsys.readouterr().out
    assert exit_code == 0
    assert re.search(r"0 │ video +│ h264", output)
    assert re.search(r"9 │ video +│ mjpeg", output)
    assert re.search(r"eng +│ 8 +│ 7.1", output)
    assert re.search(r"1920 +│ 1080 +│ Test", output)
    assert SCAN_LINE_TEMPLATE.format(verdict=scan_verdict(scan)) in output


def test_inspect_title_shows_file_size(capsys, video_path, mock_video_probe):
    """Verify the stream table title reports the file's size."""
    # Given: A video file with a known size on disk
    video_path.write_bytes(b"\0" * 2000)
    # Point the probe at the video so its size is read from the file just written
    mock_video_probe("reference.json").path_to_file = video_path

    # When: Inspecting the file
    args = ["inspect", str(video_path)]
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The title carries the human readable size
    assert exit_code == 0
    assert "(2.0 kB)" in output


def test_inspect_json_writes_one_document_for_two_files(
    capsys, tmp_path, mock_video_probe, mock_interlace_detection
):
    """Verify inspect --json prints one document holding every file's raw ffprobe data."""
    # Given: Two video files that probe as the reference recording
    first, second = tmp_path / "first.mkv", tmp_path / "second.mkv"
    first.touch()
    second.touch()
    mock_video_probe("reference.json")
    mock_interlace_detection.return_value = InterlaceScan(
        interlaced=True, field_order=FieldOrder.BFF
    )

    # When: Inspecting both with --json
    exit_code = run_vidcleaner(["inspect", "--json", str(first), str(second)])

    # Then: stdout parses as one report with both files
    doc = read_json_document(capsys)
    assert exit_code == 0
    assert doc["command"] == "inspect"
    assert len(doc["files"]) == 2
    assert doc["files"][0]["streams"] == load_ffprobe_json("reference.json")["streams"]
    assert doc["files"][0]["interlaced"] is True
    assert doc["files"][0]["field_order"] == "bff"
    assert doc["summary"] == {"total": 2, "skipped": 0}


@pytest.mark.parametrize("json_flags", [["--json"], []], ids=["json", "text"])
def test_inspect_continues_past_an_unreadable_file(
    capsys, tmp_path, mock_unreadable_probe, json_flags
):
    """Verify inspect reports an unreadable file, inspects the rest, and exits 1."""
    # Given: One unreadable file followed by a readable one
    bad, good = tmp_path / "bad.mkv", tmp_path / "good.mkv"
    bad.touch()
    good.touch()
    mock_unreadable_probe("bad.mkv")

    # When: Inspecting both
    exit_code = run_vidcleaner(["inspect", *json_flags, str(bad), str(good)])

    # Then: The readable file is shown and the run exits 1
    captured = capsys.readouterr()
    assert exit_code == 1
    if json_flags:
        doc = json.loads(captured.out)
        assert [f["name"] for f in doc["files"]] == ["good.mkv"]
        assert doc["skipped"] == [
            {"path": str(bad), "reason": "Invalid data found when processing input"}
        ]
    else:
        assert re.search(r"0 │ video +│ h264", captured.out)
        assert "bad.mkv" in captured.err


def test_json_run_does_not_leak_stderr_console_into_next_run(capsys, video_path, mock_video_probe):
    """Verify a JSON run leaves later text runs printing to stdout."""
    # Given: A JSON run has finished
    mock_video_probe("reference.json")
    run_vidcleaner(["inspect", "--json", str(video_path)])
    capsys.readouterr()

    # When: A text run follows
    run_vidcleaner(["inspect", str(video_path)])

    # Then: Its table is on stdout
    assert re.search(r"0 │ video +│ h264", capsys.readouterr().out)


def test_inspect_json_skips_a_file_it_cannot_stat(mocker, capsys, tmp_path, mock_video_probe):
    """Verify inspect --json records a file whose size cannot be read and inspects the rest."""
    # Given: Two readable files, one of which loses its permissions after the path check
    bad, good = tmp_path / "bad.mkv", tmp_path / "good.mkv"
    bad.touch()
    good.touch()
    mock_video_probe("reference.json")
    describe = inspect_video.file_object

    def file_object(video):
        if video.path.name == bad.name:
            raise PermissionError(13, "Permission denied", str(video.path))
        return describe(video)

    mocker.patch("vid_cleaner.cli.inspect_video.file_object", side_effect=file_object)

    # When: Inspecting both with --json
    exit_code = run_vidcleaner(["inspect", "--json", str(bad), str(good)])

    # Then: The readable file is reported, the other is skipped with its reason, and the run exits 1
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert [f["name"] for f in doc["files"]] == ["good.mkv"]
    assert doc["summary"] == {"total": 2, "skipped": 1}
    assert doc["skipped"][0]["path"] == str(bad)
    assert "Permission denied" in doc["skipped"][0]["reason"]
