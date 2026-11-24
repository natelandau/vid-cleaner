"""Test the vidcleaner clip subcommand."""

import pytest

from tests.helpers import run_vidcleaner
from vid_cleaner.constants import NO_VIDEO_STREAMS_REASON
from vid_cleaner.controllers import TempFile


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--start", "0:0"], "`--start` must be in format HH:MM:SS"),
        (["--duration", "0:0"], "`--duration` must be in format HH:MM:SS"),
    ],
)
def test_clip_option_errors(capsys, video_path, args, expected):
    """Verify clip command validates time format arguments."""
    # Given: Invalid time format arguments
    args = ["clip", *args, str(video_path)]

    # When: Running clip command with invalid arguments
    exit_code = run_vidcleaner(args)

    # Then: Error message is displayed
    output = capsys.readouterr().err
    assert exit_code == 1
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
def test_clipping_video(mocker, capsys, video_path, mock_ffmpeg, args, expected, mock_video_probe):
    """Verify clip command extracts video segment with specified time range."""
    # Given: Mock video file and time range arguments
    args = ["clip", *args, str(video_path)]

    # And: Mocked video metadata and output path

    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )

    # When: Running clip command
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # THEN verify ffmpeg was called with correct parameters
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    assert expected in command

    # And: Success message is displayed
    assert exit_code == 0
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
def test_clipping_video_dryrun(mocker, capsys, video_path, mock_ffmpeg, args, mock_video_probe):
    """Verify clip command dry-run shows command without execution."""
    # Given: Mock video file and dry-run flag
    args = ["clip", "-n", *args, str(video_path)]

    # And: Mocked video metadata and output path
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )

    # When: Running clip command in dry-run mode
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # THEN verify ffmpeg was not called
    mock_ffmpeg.assert_not_called()
    assert exit_code == 0
    assert "dry run" in output
    assert "clipped_video.mkv" not in output


def test_clip_cleanup_failure_still_reports_the_save(
    mocker, video_path, capsys, mock_ffmpeg, mock_video_probe
):
    """Verify a cleanup error after a successful clip is a warning, not a crash.

    The clip is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or propagate out of main().
    """
    # Given: a clip that writes successfully but whose temp-directory housekeeping raises
    args = ["clip", str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clip
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The save survives, the cleanup failure is a warning, and the run still succeeds
    assert exit_code == 0
    assert "clipped_video.mkv" in output
    assert "temp dir busy" in output


def test_clip_rejects_file_without_video_streams(video_path, capsys, mock_ffmpeg, mock_video_probe):
    """Verify clipping a file with no video streams fails cleanly instead of crashing.

    Copying a time range out of an audio-only file produces an output nobody asked for,
    so the file is refused before ffmpeg runs.
    """
    # Given: A file that probes cleanly but carries only audio
    args = ["clip", str(video_path)]
    mock_video_probe("audio_only.json")

    # When: Running clip
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: ffmpeg never runs, the reason is reported, and the run fails
    mock_ffmpeg.assert_not_called()
    assert exit_code == 1
    assert NO_VIDEO_STREAMS_REASON in captured.out + captured.err


def test_clip_continues_past_a_bad_file(
    mocker, mock_ffprobe_box, video_path, capsys, mock_ffmpeg, tmp_path
):
    """Verify one unusable file does not discard the files queued behind it."""
    # Given: An audio-only file named ahead of a good one
    good = tmp_path / "good.mp4"
    good.touch()
    args = ["clip", str(video_path), str(good)]

    # Build both boxes up front and stamp each with the path it describes: `VideoFile`
    # compares `path_to_file` to decide whether its cached probe is still current.
    audio_box = mock_ffprobe_box("audio_only.json")
    audio_box.path_to_file = video_path
    video_box = mock_ffprobe_box("reference.json")
    video_box.path_to_file = good
    boxes = {video_path: audio_box, good: video_box}
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=lambda path: boxes[path],
    )
    mocker.patch(
        "vid_cleaner.cli.clip_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to clipped_video.mkv"]),
    )

    # When: Running clip over both
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The good file was still clipped, and the run reports the failure
    mock_ffmpeg.assert_called_once()
    assert exit_code == 1
    assert NO_VIDEO_STREAMS_REASON in captured.out + captured.err
    assert "clipped_video.mkv" in captured.out
