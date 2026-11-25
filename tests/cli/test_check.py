"""Test the vidcleaner check subcommand."""

from pathlib import Path

import pytest
from cappa.base import invoke

from tests.helpers import read_json_document, run_vidcleaner
from vid_cleaner.cli.check_video import display_path
from vid_cleaner.cli.discovery_output import (
    FROM_WITH_FILES_ERROR,
    NO_FILES_OR_FROM_ERROR,
    NO_VIDEO_FILES_FOUND,
    NOT_AN_EXISTING_DIRECTORY_ERROR,
    REQUIRES_FROM_ERROR,
)
from vid_cleaner.controllers.validation import CheckResult
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.vidcleaner import VidCleaner, configure_output


@pytest.fixture(autouse=True)
def run_from_tmp_path(tmp_path, monkeypatch):
    """Run from `tmp_path` so checked files print as bare names.

    Working from the directory under test keeps the printed lines short enough that Rich
    does not soft-wrap them, so assertions can match a whole line.
    """
    monkeypatch.chdir(tmp_path)


def test_check_valid_file_exits_zero(capsys, video_path, mock_video_probe):
    """Verify a readable video file passes and the command exits zero."""
    # Given: A file that probes cleanly and has a video stream
    mock_video_probe("reference.json")

    # When: Checking it
    args = ["check", str(video_path)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: It is reported valid and the run succeeds
    assert exit_code == 0
    assert "test_video.mp4" in captured.out
    assert "1 valid, 0 invalid" in captured.out


def test_check_invalid_file_exits_one(capsys, video_path, mocker):
    """Verify an unreadable file fails the run with its ffprobe reason."""
    # Given: A file ffprobe cannot read
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(path=video_path, reason="moov atom not found"),
    )

    # When: Checking it
    args = ["check", str(video_path)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The failure carries ffprobe's reason and the run fails
    assert exit_code == 1
    assert "moov atom not found" in captured.err
    assert "0 valid, 1 invalid" in captured.out


def test_check_reports_every_file_in_a_mixed_batch(capsys, tmp_path, mock_unreadable_probe):
    """Verify one bad file never hides the verdict on the files beside it."""
    # Given: A good file, a broken file, a missing path, and a non-video file
    good = tmp_path / "good.mkv"
    good.touch()
    broken = tmp_path / "broken.mkv"
    broken.touch()
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    missing = tmp_path / "missing.mkv"

    mock_unreadable_probe(broken.name)

    # When: Checking all four at once
    args = ["check", str(good), str(broken), str(notes), str(missing)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()
    output = captured.out + captured.err

    # Then: Every file is accounted for and the run fails
    assert exit_code == 1
    assert "good.mkv" in output
    assert "Invalid data found" in output
    assert "not a video file" in output
    assert "file does not exist" in output
    assert "1 valid, 3 invalid" in captured.out


def test_check_skips_named_macos_sidecars(capsys, video_path, mock_video_probe):
    """Verify a `._` metadata file named beside a video is skipped rather than reported invalid."""
    # Given: A valid video and its AppleDouble sidecar
    mock_video_probe("reference.json")
    sidecar = video_path.with_name(f"._{video_path.name}")
    sidecar.write_bytes(b"\x00\x05\x16\x07")

    # When: Checking both, as a shell glob would name them
    exit_code = run_vidcleaner(["check", str(sidecar), str(video_path)])

    captured = capsys.readouterr()

    # Then: Only the video is checked
    assert exit_code == 0
    assert "Checked 1 file(s): 1 valid, 0 invalid" in captured.out
    assert sidecar.name not in captured.out + captured.err


def test_check_audio_only_file_fails(capsys, video_path, mock_video_probe):
    """Verify a file that probes cleanly but holds no video stream fails."""
    # Given: A file whose only stream is audio
    mock_video_probe("audio_only.json")

    # When: Checking it
    args = ["check", str(video_path)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: It fails for lacking video
    assert exit_code == 1
    assert "no video stream" in captured.err


def test_check_deep_decodes_every_file(capsys, video_path, mocker, mock_video_probe):
    """Verify `--deep` runs a full decode over each file."""
    # Given: A file that probes cleanly but fails to decode
    mock_video_probe("reference.json")
    decode = mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value="Error while decoding stream #0:0",
    )

    # When: Checking it deeply
    args = ["check", "--deep", str(video_path)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The decode ran and its error is the reason
    decode.assert_called_once()
    assert exit_code == 1
    assert "Error while decoding stream #0:0" in captured.err


def test_check_without_deep_does_not_decode(video_path, mocker, mock_video_probe):
    """Verify the default run stays metadata-only."""
    # Given: A file that probes cleanly
    mock_video_probe("reference.json")
    decode = mocker.patch("vid_cleaner.controllers.validation.decode_check")

    # When: Checking it without `--deep`
    args = ["check", str(video_path)]
    run_vidcleaner(args)

    # Then: No decode was attempted
    decode.assert_not_called()


@pytest.mark.parametrize(
    ("filters", "summary"),
    [
        pytest.param([], "1 valid, 1 invalid", id="no filter"),
        pytest.param(["--filters", "vp9"], "0 valid, 1 invalid", id="filter matching neither"),
    ],
)
def test_check_from_reports_unreadable_files(
    capsys, tmp_path, mock_unreadable_probe, filters, summary
):
    """Verify `--from` reports every file it cannot read, even when a filter excludes the rest.

    An unreadable file carries no traits, so letting a filter exclude it would let
    `check` exit zero over the very files it exists to find.
    """
    # Given: A directory holding one readable file and one that cannot be probed
    library = tmp_path / "library"
    library.mkdir()
    good = library / "good.mkv"
    good.write_bytes(b"\0" * 100)
    broken = library / "broken.mkv"
    broken.write_bytes(b"\0" * 100)
    mock_unreadable_probe(broken.name).path_to_file = good

    # When: Checking the directory
    exit_code = run_vidcleaner(["check", "--from", str(library), *filters])

    # Then: The unreadable file is reported and the run fails
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Invalid data found" in captured.err
    assert summary in captured.out


def test_check_from_empty_directory_exits_zero(capsys, tmp_path):
    """Verify a directory holding no video files is not a failure."""
    # Given: An empty directory
    library = tmp_path / "library"
    library.mkdir()

    # When: Checking it
    args = ["check", "--from", str(library)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The run reports nothing to do and succeeds
    assert exit_code == 0
    assert NO_VIDEO_FILES_FOUND in captured.out + captured.err


def test_check_from_nonexistent_directory_errors(capsys, tmp_path):
    """Verify a mistyped `--from` fails loudly instead of reporting nothing to check."""
    # Given: A path that is not a directory
    missing = tmp_path / "not_here"

    # When: Checking it
    args = ["check", "--from", str(missing)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The bad path is named
    assert exit_code == 1
    assert NOT_AN_EXISTING_DIRECTORY_ERROR in captured.err


def test_check_rejects_from_combined_with_files(capsys, tmp_path, video_path):
    """Verify the explicit and discovery modes cannot be mixed."""
    # Given: Both a file path and a `--from` directory
    args = ["check", str(video_path), "--from", str(tmp_path)]

    # When: Checking
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The conflict is reported
    assert exit_code == 1
    assert FROM_WITH_FILES_ERROR in captured.err


@pytest.mark.parametrize(
    "discovery_arg",
    [["--filters", "h264"], ["--depth", "1"], ["--reverse"], ["--sort", "size"]],
)
def test_check_rejects_discovery_flags_without_from(capsys, video_path, discovery_arg):
    """Verify discovery flags without `--from` are an error rather than a silent no-op."""
    # Given: A discovery flag applied to explicitly named files
    args = ["check", *discovery_arg, str(video_path)]

    # When: Checking
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The requirement is reported
    assert exit_code == 1
    assert REQUIRES_FROM_ERROR in captured.err


def test_check_with_no_arguments_errors(capsys):
    """Verify checking nothing is an error rather than a vacuous success."""
    # Given: No files and no `--from`
    args = ["check"]

    # When: Checking
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The user is told what to provide
    assert exit_code == 1
    assert NO_FILES_OR_FROM_ERROR in captured.err


def test_check_does_not_accept_limit():
    """Verify `--limit` is absent, so a truncated selection cannot mask unchecked files."""
    # Given: A limit applied to a check run
    args = ["check", "--from", ".", "--limit", "5"]

    # When: Checking
    exit_code = run_vidcleaner(args)

    # Then: The flag is rejected at parse time
    assert exit_code == 2


def test_check_prints_square_brackets_verbatim(capsys, tmp_path, mocker):
    """Verify bracketed filenames and ffmpeg reasons survive rendering intact.

    Release names and ffmpeg diagnostics both carry square brackets, which Rich reads as
    markup tags and drops, leaving a printed filename that does not exist on disk.
    """
    # Given: A bracketed filename that fails with a bracketed ffprobe reason
    movie = tmp_path / "Movie [x265].mkv"
    movie.touch()
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(
            path=movie, reason="[matroska,webm @ 0x14f1] Read error at pos. 12345"
        ),
    )

    # When: Checking it
    args = ["check", str(movie)]
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: Both the name and the reason are printed as written
    assert exit_code == 1
    assert "Movie [x265].mkv" in captured.err
    assert "[matroska,webm @ 0x14f1] Read error" in captured.err


def test_display_path_uses_bare_name_in_cwd(tmp_path):
    """Verify a file in the working directory prints as a bare filename."""
    # Given: A file in the current working directory
    path = tmp_path / "movie.mkv"
    path.touch()

    # When: Rendering it
    # Then: Only the name is shown
    assert display_path(path) == "movie.mkv"


def test_display_path_is_relative_below_cwd(tmp_path):
    """Verify a file in a subdirectory prints as a path relative to the working directory."""
    # Given: A file one level below the working directory
    nested = tmp_path / "movies" / "movie.mkv"
    nested.parent.mkdir()
    nested.touch()

    # When: Rendering it
    # Then: The subdirectory is shown so the name is not ambiguous
    assert display_path(nested) == str(Path("movies") / "movie.mkv")


def test_display_path_is_absolute_outside_cwd(tmp_path, monkeypatch):
    """Verify a file outside the working directory prints as an absolute path."""
    # Given: A working directory that does not contain the file
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    outside = tmp_path / "movie.mkv"
    outside.touch()

    # When: Rendering it
    # Then: The full path is shown
    assert display_path(outside) == str(outside.resolve())


def test_display_path_survives_a_deleted_working_directory(tmp_path, monkeypatch):
    """Verify a deleted working directory does not crash the report."""
    # Given: A working directory that no longer exists
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    workdir.rmdir()
    movie = tmp_path / "movie.mkv"

    # When: Rendering a file found by absolute path
    # Then: The path is shown as found
    assert display_path(movie) == str(movie)


@pytest.mark.parametrize(
    ("readable", "exit_code", "summary", "ok", "has_reason"),
    [
        (True, 0, {"total": 1, "valid": 1, "invalid": 0}, True, False),
        (False, 1, {"total": 1, "valid": 0, "invalid": 1}, False, True),
    ],
    ids=["valid", "unreadable"],
)
def test_check_json(
    capsys,
    video_path,
    mock_video_probe,
    mock_unreadable_probe,
    readable,
    exit_code,
    summary,
    ok,
    has_reason,
):
    """Verify check --json prints one document with a verdict per file."""
    # Given: A file that probes or fails to probe
    if readable:
        mock_video_probe("reference.json")
    else:
        mock_unreadable_probe(video_path.name)

    # When: Checking it with --json, named relative to the working directory
    actual_exit_code = run_vidcleaner(["check", "--json", video_path.name])

    # Then: stdout is one report matching the verdict, and the exit code is unchanged
    doc = read_json_document(capsys)
    assert actual_exit_code == exit_code
    assert doc["command"] == "check"
    assert doc["error"] is None
    assert doc["summary"] == summary
    assert doc["files"][0]["path"] == str(video_path.resolve())
    assert doc["files"][0]["ok"] is ok
    assert (doc["files"][0]["reason"] is not None) is has_reason


def test_check_json_reports_missing_from_directory(capsys, tmp_path):
    """Verify a missing --from directory is the report's error."""
    # Given: A directory that does not exist
    missing = tmp_path / "missing"

    # When: Checking it with --json
    exit_code = run_vidcleaner(["check", "--json", "--from", str(missing)])

    # Then: The run fails with the reason and no files
    doc = read_json_document(capsys)
    assert exit_code == 1
    assert doc["files"] == []
    assert NOT_AN_EXISTING_DIRECTORY_ERROR in doc["error"]


def test_check_json_empty_directory_has_no_error(capsys, tmp_path):
    """Verify a directory with no video files is a successful, empty report."""
    # Given: An empty directory
    # When: Checking it with --json
    exit_code = run_vidcleaner(["check", "--json", "--from", str(tmp_path)])

    # Then: The run succeeds with no error and no files
    doc = read_json_document(capsys)
    assert exit_code == 0
    assert doc["error"] is None
    assert doc["files"] == []


def test_check_json_interrupt_keeps_finished_verdicts(capsys, mocker, tmp_path):
    """Verify an interrupted check reports the verdicts it finished."""
    # Given: Two files, where checking the second is interrupted
    first, second = tmp_path / "first.mkv", tmp_path / "second.mkv"
    first.touch()
    second.touch()
    mocker.patch(
        "vid_cleaner.cli.check_video.check_video_file",
        side_effect=[CheckResult(path=first, ok=True), KeyboardInterrupt()],
    )

    # When: Checking both with --json
    with pytest.raises(KeyboardInterrupt):
        invoke(
            obj=VidCleaner,
            argv=["check", "--json", str(first), str(second)],
            deps=[configure_output],
        )

    # Then: The document marks the interrupt and holds the finished verdict
    doc = read_json_document(capsys)
    assert doc["interrupted"] is True
    assert [entry["path"] for entry in doc["files"]] == [str(first)]
    assert doc["summary"] == {"total": 1, "valid": 1, "invalid": 0}
