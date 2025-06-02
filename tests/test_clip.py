"""Test the vidcleaner clip subcommand."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--start", "0:0"], "Error: --start must be in format HH:MM:SS"),
        (["--duration", "0:0"], "Error: --duration must be in format HH:MM:SS"),
    ],
)
def test_clip_option_errors(debug, tmp_path, clean_stdout, mock_video_path, args, expected):
    """Verify clip command validates time format arguments."""
    # Given: Invalid time format arguments
    args = ["clip", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # When: Running clip command with invalid arguments
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Error message is displayed
    output = clean_stdout()
    assert exc_info.value.code == 1
    assert expected in output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], "-ss 00:00:00 -t 00:01:00 -map 0"),
        (["--start", "00:05:00"], "-ss 00:05:00 -t 00:01:00 -map 0"),
        (["--start", "00:05:00", "--duration", "00:10:00"], "-ss 00:05:00 -t 00:10:00 -map 0"),
        (["--duration", "00:10:00"], "-ss 00:00:00 -t 00:10:00 -map 0"),
    ],
)
def test_clipping_video(
    mocker,
    mock_ffprobe_box,
    clean_stdout,
    mock_video_path,
    tmp_path,
    mock_ffmpeg,
    debug,
    args,
    expected,
):
    """Verify clip command extracts video segment with specified time range."""
    # Given: Mock video file and time range arguments
    args = ["clip", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # And: Mocked video metadata and output path

    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch("vid_cleaner.cli.clip_video.copy_file", return_value="clipped_video.mkv")

    # When: Running clip command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg was called with correct parameters
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    assert expected in command

    # And: Success message is displayed
    assert exc_info.value.code == 0
    assert "✅ Success: clipped_video.mkv" in output


@pytest.mark.parametrize(
    ("args"),
    [
        ([]),
        (["--start", "00:05:00"]),
        (["--start", "00:05:00", "--duration", "00:10:00"]),
        (["--duration", "00:10:00"]),
    ],
)
def test_clipping_video_dryrun(
    mocker,
    clean_stdout,
    mock_ffprobe_box,
    mock_video_path,
    tmp_path,
    mock_ffmpeg,
    debug,
    args,
):
    """Verify clip command dry-run shows command without execution."""
    # Given: Mock video file and dry-run flag
    args = ["clip", "-n", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # And: Mocked video metadata and output path
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch("vid_cleaner.cli.clip_video.copy_file", return_value="clipped_video.mkv")

    # When: Running clip command in dry-run mode
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg was not called
    mock_ffmpeg.assert_not_called()
    assert exc_info.value.code == 0
    assert "dry run" in output
    assert "✅ Success: clipped_video.mkv" not in output
