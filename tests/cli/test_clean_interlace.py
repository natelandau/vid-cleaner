"""Test how the clean subcommand asks about interlaced video before it encodes."""

from pathlib import Path

import pytest
from cappa.base import invoke

from tests.helpers import read_json_document, run_vidcleaner
from vid_cleaner.cli.clean_video import FILES_FAILED_ERROR
from vid_cleaner.cli.interlace_question import (
    DEINTERLACE_CHOICE,
    DEINTERLACE_FLAG_REQUIRED_ERROR,
    INTERLACED_FILES_SKIPPED_REASON,
    REENCODE_CHOICE,
    SKIP_CHOICE,
)
from vid_cleaner.constants import INTERLACE_QUESTION_NOTICE
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils.interlace_detection import FieldOrder, InterlaceScan
from vid_cleaner.vidcleaner import VidCleaner, configure_output

INTERLACED_TFF = InterlaceScan(interlaced=True, field_order=FieldOrder.TFF)
PROMPT_TARGET = "vid_cleaner.cli.interlace_question.Prompt.ask"


@pytest.fixture
def two_videos(tmp_path):
    """Create two empty video files to clean in one run.

    Returns:
        list[str]: The two file paths as command-line arguments.
    """
    paths = [tmp_path / "first.mkv", tmp_path / "second.mkv"]
    for path in paths:
        path.touch()
    return [str(path) for path in paths]


def _ffmpeg_commands(mock_ffmpeg) -> list[str]:
    """Return every ffmpeg command a run issued, each joined into one string."""
    return [" ".join(call.args[0]) for call in mock_ffmpeg.call_args_list]


@pytest.mark.parametrize(
    ("answer", "expect_filter", "expect_encoded"),
    [
        (DEINTERLACE_CHOICE, True, True),
        (REENCODE_CHOICE, False, True),
        (SKIP_CHOICE, False, False),
    ],
)
def test_clean_asks_once_for_interlaced_files(
    mocker,
    two_videos,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    interactive_console,
    mock_interlace_detection,
    answer,
    expect_filter,
    expect_encoded,
):
    """Verify one answer decides how every interlaced file in the run is handled."""
    # Given: two interlaced files that --h265 re-encodes, and a user who answers the question
    mock_video_probe("reference.json")
    mock_interlace_detection.return_value = INTERLACED_TFF
    interactive_console(is_terminal=True)
    ask = mocker.patch(PROMPT_TARGET, return_value=answer)

    # When: cleaning both files
    exit_code = run_vidcleaner(["clean", "--h265", *two_videos])

    # Then: the question was asked once, and the answer applies to both files
    assert exit_code == 0
    ask.assert_called_once()
    commands = _ffmpeg_commands(mock_ffmpeg)
    assert len(commands) == (2 if expect_encoded else 0)
    assert all(("bwdif=" in command) is expect_filter for command in commands)


@pytest.mark.parametrize(
    ("use_from", "is_terminal", "prompt_error"),
    [
        pytest.param(True, True, None, id="--yes with --from"),
        pytest.param(False, False, None, id="non-terminal console"),
        pytest.param(False, True, EOFError, id="EOF from the prompt"),
    ],
)
def test_clean_stops_before_encoding_when_it_cannot_ask(
    mocker,
    capsys,
    video_library,
    mock_ffmpeg,
    mock_original_language,
    interactive_console,
    mock_interlace_detection,
    use_from,
    is_terminal,
    prompt_error,
):
    """Verify a run that cannot ask about interlaced files stops before any encode."""
    # Given: an interlaced file that --h265 re-encodes, and a run that cannot get an answer
    directory = video_library([("apple.mkv", 100, 1_000_000)])
    mock_interlace_detection.return_value = INTERLACED_TFF
    interactive_console(is_terminal=is_terminal)
    mocker.patch(PROMPT_TARGET, side_effect=prompt_error)
    target = ["--from", str(directory), "--yes"] if use_from else [str(directory / "apple.mkv")]

    # When: cleaning
    exit_code = run_vidcleaner(["clean", "--h265", *target])

    # Then: the run stops with an error naming both flags, and nothing is encoded
    assert exit_code == 1
    # Rich wraps long lines to the console width
    assert DEINTERLACE_FLAG_REQUIRED_ERROR in " ".join(capsys.readouterr().err.split())
    mock_ffmpeg.assert_not_called()


@pytest.mark.parametrize(
    ("flag", "expect_filter"), [("--deinterlace", True), ("--no-deinterlace", False)]
)
def test_clean_with_a_deinterlace_flag_does_not_ask(
    mocker,
    video_path,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    interactive_console,
    mock_interlace_detection,
    flag,
    expect_filter,
):
    """Verify a deinterlace flag answers the question in advance."""
    # Given: an interlaced file that --h265 re-encodes on an interactive console
    mock_video_probe("reference.json")
    mock_interlace_detection.return_value = INTERLACED_TFF
    interactive_console(is_terminal=True)
    ask = mocker.patch(PROMPT_TARGET)

    # When: cleaning with the flag
    exit_code = run_vidcleaner(["clean", "--h265", flag, str(video_path)])

    # Then: nothing is asked, and the flag decides whether the video is deinterlaced
    assert exit_code == 0
    ask.assert_not_called()
    [command] = _ffmpeg_commands(mock_ffmpeg)
    assert ("bwdif=" in command) is expect_filter


def test_clean_does_not_scan_files_it_will_not_reencode(
    mocker,
    video_path,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    mock_interlace_detection,
):
    """Verify a run that keeps the video stream as it is spends no time on an interlace scan."""
    # Given: a file and no flag that re-encodes its video
    mock_video_probe("reference.json")
    ask = mocker.patch(PROMPT_TARGET)

    # When: cleaning it
    exit_code = run_vidcleaner(["clean", str(video_path)])

    # Then: the file was never scanned and nothing was asked
    assert exit_code == 0
    mock_interlace_detection.assert_not_called()
    ask.assert_not_called()


@pytest.mark.parametrize(
    "scan_error",
    [
        pytest.param(VideoProbeError(path=Path("x.mkv"), reason="unreadable"), id="probe error"),
        pytest.param(OSError(5, "Input/output error"), id="os error"),
    ],
)
def test_clean_reports_a_file_whose_scan_fails_and_cleans_the_rest(
    mocker,
    capsys,
    two_videos,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    interactive_console,
    mock_interlace_detection,
    scan_error,
):
    """Verify a failed interlace scan fails only its own file, and asks nothing."""
    # Given: two files that --h265 re-encodes, where scanning the first fails
    first, second = two_videos
    mock_video_probe("reference.json")

    def scan(path, **_kwargs):
        if str(path) == first:
            raise scan_error
        return InterlaceScan(interlaced=False, field_order=None)

    mock_interlace_detection.side_effect = scan
    interactive_console(is_terminal=True)
    ask = mocker.patch(PROMPT_TARGET)

    # When: cleaning both
    exit_code = run_vidcleaner(["clean", "--h265", *two_videos])

    # Then: the first file is reported as failed, the second is encoded, and nothing is asked
    captured = capsys.readouterr()
    assert exit_code == 1
    assert FILES_FAILED_ERROR in captured.out + captured.err
    ask.assert_not_called()
    [command] = _ffmpeg_commands(mock_ffmpeg)
    assert Path(second).stem in command


def test_clean_dry_run_lists_interlaced_files_without_asking(
    mocker,
    capsys,
    video_path,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    interactive_console,
    mock_interlace_detection,
):
    """Verify a dry run names the interlaced files and previews no deinterlace step."""
    # Given: an interlaced file that --h265 re-encodes on an interactive console
    mock_video_probe("reference.json")
    mock_interlace_detection.return_value = INTERLACED_TFF
    interactive_console(is_terminal=True)
    ask = mocker.patch(PROMPT_TARGET)

    # When: previewing the clean
    exit_code = run_vidcleaner(["-n", "clean", "--h265", str(video_path)])

    # Then: the file and its field order are listed, a real run's question is noted, and
    # nothing is asked or encoded
    captured = capsys.readouterr()
    output = " ".join((captured.out + captured.err).split())
    assert exit_code == 0
    assert video_path.name in output
    assert FieldOrder.TFF.description in output
    assert INTERLACE_QUESTION_NOTICE in output
    assert "bwdif=" not in output
    ask.assert_not_called()
    mock_ffmpeg.assert_not_called()


def test_clean_json_lists_skipped_interlaced_files(
    mocker,
    capsys,
    video_path,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    interactive_console,
    mock_interlace_detection,
):
    """Verify the JSON report lists files the user chose to skip, without counting a failure."""
    # Given: an interlaced file that --h265 re-encodes, and a user who skips it
    mock_video_probe("reference.json")
    mock_interlace_detection.return_value = INTERLACED_TFF
    interactive_console(is_terminal=True)
    mocker.patch(PROMPT_TARGET, return_value=SKIP_CHOICE)

    # When: cleaning with a JSON report
    exit_code = run_vidcleaner(["clean", "--json", "--h265", str(video_path)])

    # Then: the file is listed as skipped with the reason, and nothing was encoded
    document = read_json_document(capsys)
    assert exit_code == 0
    assert document["skipped"] == [
        {"path": str(video_path), "reason": INTERLACED_FILES_SKIPPED_REASON}
    ]
    assert document["files"] == []
    mock_ffmpeg.assert_not_called()


def test_clean_interrupt_during_scan_encodes_nothing(
    video_path, mock_ffmpeg, mock_video_probe, mock_original_language, mock_interlace_detection
):
    """Verify Ctrl-C during the interlace scan stops the run before any encode."""
    # Given: a file that --h265 re-encodes and a scan the user interrupts
    mock_video_probe("reference.json")
    mock_interlace_detection.side_effect = KeyboardInterrupt

    # When: cleaning it
    # Then: the interrupt stops the run and nothing is encoded
    with pytest.raises(KeyboardInterrupt):
        invoke(obj=VidCleaner, argv=["clean", "--h265", str(video_path)], deps=[configure_output])
    mock_ffmpeg.assert_not_called()
