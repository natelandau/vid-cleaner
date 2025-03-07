"""Test the vidcleaner clip subcommand."""

from pathlib import Path

import cappa
import pytest
from iso639 import Lang

from vid_cleaner.vidcleaner import VidCleaner

from vid_cleaner.models.video_file import VideoFile  # isort: skip
from vid_cleaner.utils import settings


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--start", "0:0"], "Error: --start must be in format HH:MM:SS"),
        (["--duration", "0:0"], "Error: --duration must be in format HH:MM:SS"),
    ],
)
def test_clip_option_errors(debug, tmp_path, clean_stdout, mock_video_path, args, expected):
    """Test that the command fails when a flag conflict is detected."""
    args = ["clip", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "keep_languages": ["en"]})

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    # debug(output, "output")

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
    mock_ffprobe,
    clean_stdout,
    mock_video_path,
    tmp_path,
    mock_ffmpeg,
    debug,
    args,
    expected,
):
    """Verify clip command correctly processes video with given start time and duration."""
    args = ["clip", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "keep_languages": ["en"]})

    # GIVEN mocked video metadata and output path
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip_video.tmp_to_output", return_value="clipped_video.mkv")

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg was called with correct parameters
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])

    # AND verify command completed successfully
    assert exc_info.value.code == 0
    assert expected in command
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
    mock_ffprobe,
    mock_video_path,
    tmp_path,
    mock_ffmpeg,
    debug,
    args,
):
    """Verify dry-run mode shows expected command without executing ffmpeg."""
    args = ["clip", "-n", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "keep_languages": ["en"]})

    # GIVEN mocked video metadata and output path
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clip_video.tmp_to_output", return_value="clipped_video.mkv")

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg was not called
    mock_ffmpeg.assert_not_called()
    assert exc_info.value.code == 0
    assert "dry run" in output
    assert "✅ Success: clipped_video.mkv" not in output
