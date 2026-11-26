"""Test the JSON report the clean subcommand prints with `--json`."""

import json
from pathlib import Path

import pytest

from tests.helpers import load_ffprobe_json, read_json_document, run_vidcleaner
from vid_cleaner.cli.clean_video import RESULT_PROBE_WARNING
from vid_cleaner.exceptions import VideoCleanError

OUTCOME_KEYS = [
    "path",
    "status",
    "reason",
    "original",
    "result",
    "destination",
    "planned_streams",
    "backup_path",
    "actions",
    "notices",
    "ffmpeg_command",
    "verify",
    "warnings",
]
PLANNED_STREAM_KEYS = {"source_index", "codec_type", "encoder", "filter", "metadata"}
ACTION_KEYS = {"label", "applied", "reason"}
STATUSES = ["cleaned", "unchanged", "failed", "planned"]


@pytest.fixture
def interrupt_second_encode(mocker):
    """Let the first ffmpeg pass write a real result and interrupt the second."""
    encodes: list[str] = []

    def run_ffmpeg(command, *, on_progress):
        encodes.append(str(command.output_path))
        if len(encodes) > 1:
            raise KeyboardInterrupt
        command.output_path.write_bytes(b"transcoded")

    mocker.patch("vid_cleaner.cli.ffmpeg_step.run_ffmpeg", run_ffmpeg)


@pytest.fixture
def passing_verify(mocker):
    """Pass the output check, since the recorded probe does not match the plan's stream counts."""
    return mocker.patch("vid_cleaner.cli.ffmpeg_step.verify_output", autospec=True)


@pytest.mark.parametrize(
    (
        "global_flags",
        "clean_flags",
        "setup_fixtures",
        "status",
        "has_result",
        "has_destination",
        "has_planned_streams",
        "has_ffmpeg_command",
        "verify",
    ),
    [
        (["-n"], [], [], "planned", False, True, True, True, None),
        (
            [],
            ["--verify=quick"],
            ["fake_transcode", "passing_verify"],
            "cleaned",
            True,
            True,
            False,
            True,
            "quick",
        ),
        ([], [], ["captured_clean"], "unchanged", False, False, False, False, None),
        (["-n"], [], ["captured_clean"], "unchanged", False, False, False, False, None),
    ],
    ids=["planned", "cleaned", "unchanged", "unchanged-dry-run"],
)
def test_clean_json_reports_each_status(
    request,
    capsys,
    tmp_path,
    mock_video_probe,
    mock_original_language,
    global_flags,
    clean_flags,
    setup_fixtures,
    status,
    has_result,
    has_destination,
    has_planned_streams,
    has_ffmpeg_command,
    verify,
) -> None:
    """Verify clean --json reports one outcome per file with the fields its status sets."""
    # Given: A file whose clean ends with the given status
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"original")
    mock_video_probe("reference.json")
    for fixture_name in setup_fixtures:
        request.getfixturevalue(fixture_name)

    # When: Cleaning it with --json
    exit_code = run_vidcleaner(
        [*global_flags, "clean", "--json", "--h265", *clean_flags, str(video)]
    )

    # Then: stdout holds one document whose outcome matches the status
    doc = read_json_document(capsys)
    outcome = doc["files"][0]
    reference = load_ffprobe_json("reference.json")
    assert exit_code == 0
    assert doc["command"] == "clean"
    assert doc["dry_run"] is bool(global_flags)
    assert doc["error"] is None
    assert doc["summary"] == {"total": 1, **{name: int(name == status) for name in STATUSES}}
    assert doc["skipped"] == []
    assert list(outcome) == OUTCOME_KEYS
    assert outcome["path"] == str(video)
    assert outcome["status"] == status
    assert outcome["reason"] is None
    assert outcome["original"]["path"] == str(video)
    assert outcome["original"]["streams"] == reference["streams"]
    assert outcome["result"] == (
        {
            "path": str(video),
            "name": video.name,
            "size": len(b"transcoded"),
            "streams": reference["streams"],
            "format": reference["format"],
        }
        if has_result
        else None
    )
    assert outcome["destination"] == (str(video) if has_destination else None)
    assert (outcome["planned_streams"] is not None) is has_planned_streams
    assert all(stream.keys() == PLANNED_STREAM_KEYS for stream in outcome["planned_streams"] or [])
    assert (outcome["ffmpeg_command"] is not None) is has_ffmpeg_command
    assert str(video) in (outcome["ffmpeg_command"] or [str(video)])
    assert outcome["verify"] == verify
    assert all(action.keys() == ACTION_KEYS for action in outcome["actions"])


def _fail_free_space_check(request, video_path: Path) -> None:
    request.getfixturevalue("mock_video_probe")("reference.json")
    request.getfixturevalue("mocker").patch(
        "vid_cleaner.cli.clean_video.check_free_space",
        side_effect=VideoCleanError(path=video_path, reason="disk full"),
    )


def _fail_probe(request, video_path: Path) -> None:
    request.getfixturevalue("mock_unreadable_probe")(video_path.name)


@pytest.mark.parametrize(
    ("make_file_fail", "reason", "has_original"),
    [
        (_fail_free_space_check, "disk full", True),
        (_fail_probe, "Invalid data found when processing input", False),
    ],
    ids=["free-space", "unreadable"],
)
def test_clean_json_failed_file(
    request,
    capsys,
    video_path,
    mock_ffmpeg,
    mock_original_language,
    make_file_fail,
    reason,
    has_original,
) -> None:
    """Verify a failed file is reported by path with its reason and leaves `error` null."""
    # Given: A file that fails before encoding
    make_file_fail(request, video_path)

    # When: Cleaning it with --json
    exit_code = run_vidcleaner(["clean", "--json", "--h265", str(video_path)])

    # Then: The outcome names the file and the reason, the run exits 1, and there is no run error
    doc = read_json_document(capsys)
    outcome = doc["files"][0]
    assert exit_code == 1
    assert doc["error"] is None
    assert doc["summary"]["failed"] == 1
    assert outcome["path"] == str(video_path)
    assert outcome["status"] == "failed"
    assert outcome["reason"] == reason
    assert (outcome["original"] is not None) is has_original
    assert outcome["destination"] is None


def test_clean_json_interrupt_keeps_finished_files(
    capsys, tmp_path, interrupt_second_encode, mock_video_probe, mock_original_language
) -> None:
    """Verify an interrupt reports the finished files and leaves out the one in progress."""
    # Given: Two files, where the second encode is interrupted
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"original")
    second.write_bytes(b"original")
    mock_video_probe("reference.json")

    # When: Cleaning both with --json
    exit_code = run_vidcleaner(["clean", "--json", "--h265", str(first), str(second)])

    # Then: The document marks the interrupt and holds only the finished file
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert doc["interrupted"] is True
    assert [outcome["original"]["path"] for outcome in doc["files"]] == [str(first)]


def test_clean_json_from_without_terminal_or_yes(capsys, video_library) -> None:
    """Verify a --from run that cannot prompt puts the refusal in `error`."""
    # Given: A discoverable library and no terminal to confirm on
    directory = video_library([("apple.mkv", 100, 1_000_000)])

    # When: Cleaning it with --json and without --yes
    exit_code = run_vidcleaner(["clean", "--json", "--from", str(directory)])

    # Then: The run fails with the refusal as the error, and stdout holds only the document
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert doc["files"] == []
    assert "--yes" in doc["error"]


@pytest.mark.parametrize(
    ("argv", "error_fragment"),
    [
        (["--h265", "--vp9"], "H265 and VP9"),
        (["--out", "out.mkv", "--from", "."], "--out"),
    ],
    ids=["h265-and-vp9", "out-with-from"],
)
def test_clean_json_reports_conflicting_flags(capsys, argv, error_fragment) -> None:
    """Verify a flag conflict that stops the run is the report's error."""
    # Given: Flags that cannot be combined
    # When: Cleaning with --json
    exit_code = run_vidcleaner(["clean", "--json", *argv])

    # Then: The run fails with the reason and no files
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert doc["files"] == []
    assert error_fragment in doc["error"]


def test_clean_json_reports_backup_path(
    capsys, tmp_path, fake_transcode, mock_video_probe, mock_original_language
) -> None:
    """Verify an in-place write reports the backup it kept of the original."""
    # Given: A file cleaned in place without --overwrite
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"original")
    mock_video_probe("reference.json")

    # When: Cleaning it with --json
    exit_code = run_vidcleaner(["clean", "--json", "--h265", str(video)])

    # Then: The backup path names a .bak copy that exists
    doc = read_json_document(capsys)
    backup_path = doc["files"][0]["backup_path"]
    assert exit_code == 0
    assert backup_path.endswith(".bak")
    assert Path(backup_path).read_bytes() == b"original"


def test_clean_json_unreadable_result_is_a_warning(
    capsys, tmp_path, fake_transcode, mock_unreadable_probe, mock_original_language
) -> None:
    """Verify a written file that cannot be probed stays cleaned, with a warning."""
    # Given: A file that cleans, but whose written result ffprobe cannot read
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"original")
    result = tmp_path / "result.mkv"
    mock_unreadable_probe(result.name)

    # When: Cleaning it to a separate output with --json
    exit_code = run_vidcleaner(["clean", "--json", "--h265", "--out", str(result), str(video)])

    # Then: The file counts as cleaned with no result, and both outputs show the warning
    captured = capsys.readouterr()
    outcome = json.loads(captured.out)["files"][0]
    assert exit_code == 0
    assert outcome["status"] == "cleaned"
    assert outcome["result"] is None
    assert any(RESULT_PROBE_WARNING in warning for warning in outcome["warnings"])
    assert RESULT_PROBE_WARNING in captured.err
