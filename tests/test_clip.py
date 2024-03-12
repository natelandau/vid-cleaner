# type: ignore
"""Test the inspect command."""

import re

import pytest
from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.vid_cleaner import app

runner = CliRunner()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--start", "0:0"], "Start must be in format HH:MM:SS "),
        (["--duration", "0:0"], "Duration must be in format HH:MM:SS "),
    ],
)
def test_clip_option_errors(mock_config, debug, mock_video, args, expected):
    """Test the clip command with invalid time options."""
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["clip", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    assert result.exit_code > 0
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
    mocker, mock_ffprobe, mock_video, mock_config, mock_ffmpeg, debug, args, expected
):
    """Test clipping a video."""
    # Setup mocks
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip.tmp_to_output", return_value="clipped_video.mkv")

    # WHEN the clip command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["clip", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    mock_ffmpeg.assert_called_once()  # Check that the ffmpeg command was called once
    args, _ = mock_ffmpeg.call_args  # Grab the ffmpeg command arguments
    command = " ".join(args[0])  # Join the arguments into a single string

    assert result.exit_code == 0
    assert expected in command
    assert "✅ clipped_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], "-ss 00:00:00 -t 00:01:00"),
        (["--start", "00:05:00"], "-ss 00:05:00 -t 00:01:00"),
        (["--start", "00:05:00", "--duration", "00:10:00"], "-ss 00:05:00 -t 00:10:00"),
        (["--duration", "00:10:00"], "-ss 00:00:00 -t 00:10:00"),
    ],
)
def test_clipping_video_dryrun(
    mocker, mock_ffprobe, mock_video, mock_config, mock_ffmpeg, debug, args, expected
):
    """Test clipping a video."""
    # Setup mocks
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip.tmp_to_output", return_value="clipped_video.mkv")

    # WHEN the clip command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["clip", "-n", *args, str(mock_video.path)])

    output = strip_ansi(result.output)

    # THEN the video should not be clipped
    mock_ffmpeg.assert_not_called()  # Check that the ffmpeg command was called once
    assert result.exit_code == 0
    assert expected in output
    assert "✅ clipped_video.mkv" not in output
