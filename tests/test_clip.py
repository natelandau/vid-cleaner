# type: ignore
"""Test the inspect command."""

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
    """Verify clip command rejects invalid time format options."""
    # GIVEN a mock configuration
    with VidCleanerConfig.change_config_sources(mock_config()):
        # WHEN invoking clip command with invalid time options
        result = runner.invoke(app, ["clip", *args, str(mock_video.path)])

    # THEN verify command fails
    output = strip_ansi(result.output)
    assert result.exit_code > 0
    # AND verify error message contains expected text
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
    """Verify clip command correctly processes video with given start time and duration."""
    # GIVEN mocked video metadata and output path
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip.tmp_to_output", return_value="clipped_video.mkv")

    # WHEN invoking clip command with specified arguments
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["clip", *args, str(mock_video.path)])

    output = strip_ansi(result.output)

    # THEN verify ffmpeg was called with correct parameters
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])

    # AND verify command completed successfully
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
    """Verify dry-run mode shows expected command without executing ffmpeg."""
    # GIVEN mocked video metadata and output path
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip.tmp_to_output", return_value="clipped_video.mkv")

    # WHEN invoking clip command with dry-run flag
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["clip", "-n", *args, str(mock_video.path)])

    output = strip_ansi(result.output)

    # THEN verify ffmpeg was not called
    mock_ffmpeg.assert_not_called()
    # AND verify command completed successfully
    assert result.exit_code == 0
    # AND verify expected command parameters are shown
    assert expected in output
    # AND verify no output file was created
    assert "✅ clipped_video.mkv" not in output
