"""Test the vidcleaner clip subcommand."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.controllers import TempFile
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--start", "0:0"], "`--start` must be in format HH:MM:SS"),
        (["--duration", "0:0"], "`--duration` must be in format HH:MM:SS"),
    ],
)
def test_clip_option_errors(debug, tmp_path, capsys, mock_video_path, args, expected):
    """Verify clip command validates time format arguments."""
    # Given: Invalid time format arguments
    args = ["clip", *args, str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # When: Running clip command with invalid arguments
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Error message is displayed
    output = capsys.readouterr().err
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
    capsys,
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
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )

    # When: Running clip command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out
    # debug(output, "output")

    # THEN verify ffmpeg was called with correct parameters
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    assert expected in command

    # And: Success message is displayed
    assert exc_info.value.code == 0
    assert "clipped_video.mkv" in output


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
    capsys,
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
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )

    # When: Running clip command in dry-run mode
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out
    # debug(output, "output")

    # THEN verify ffmpeg was not called
    mock_ffmpeg.assert_not_called()
    assert exc_info.value.code == 0
    assert "dry run" in output
    assert "clipped_video.mkv" not in output


def test_clip_cleanup_failure_still_reports_the_save(
    mocker, mock_ffprobe_box, mock_video_path, capsys, mock_ffmpeg, tmp_path
):
    """Verify a cleanup error after a successful clip is a warning, not a crash.

    The clip is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or propagate out of main().
    """
    # Given: a clip that writes successfully but whose temp-directory housekeeping raises
    args = ["clip", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clip
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The save survives, the cleanup failure is a warning, and the run still succeeds
    assert exc_info.value.code == 0
    assert "clipped_video.mkv" in output
    assert "temp dir busy" in output
