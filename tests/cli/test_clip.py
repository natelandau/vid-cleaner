"""Test the vidcleaner clip subcommand."""

import pytest

from tests.helpers import ffmpeg_command, run_vidcleaner
from vid_cleaner.cli import clip_video
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
def test_clipping_video(
    video_path, mock_ffmpeg, args, expected, mock_video_probe, stub_output_write
):
    """Verify clip command extracts video segment with specified time range."""
    # Given: A video and time range arguments
    args = ["clip", *args, str(video_path)]
    mock_video_probe("reference.json")

    # When: Running clip command
    exit_code = run_vidcleaner(args)

    # Then: ffmpeg cuts the requested range
    command = ffmpeg_command(mock_ffmpeg)
    assert expected in command

    assert exit_code == 0


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--start", "00:05:00"],
        ["--start", "00:05:00", "--duration", "00:10:00"],
        ["--duration", "00:10:00"],
    ],
)
def test_clipping_video_dryrun(
    capsys, video_path, mock_ffmpeg, args, mock_video_probe, stub_output_write
):
    """Verify clip command dry-run shows command without execution."""
    # Given: Mock video file and dry-run flag
    args = ["clip", "-n", *args, str(video_path)]

    mock_video_probe("reference.json")

    # When: Running clip command in dry-run mode
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: ffmpeg does not run and nothing is saved
    mock_ffmpeg.assert_not_called()
    assert exit_code == 0
    assert "dry run" in output
    assert "Saved to" not in output


def test_clip_out_writes_the_clip_there_and_leaves_the_source(
    tmp_path, video_path, fake_transcode, mock_video_probe
):
    """Verify `--out` receives the clip and the source video is untouched."""
    # Given: A source video and an output path beside it
    video_path.write_bytes(b"source")
    out = tmp_path / "clip.mkv"
    mock_video_probe("reference.json")

    # When: Clipping to the output path
    exit_code = run_vidcleaner(["clip", "--out", str(out), str(video_path)])

    # Then: The clip lands at --out and the source keeps its content
    assert exit_code == 0
    assert out.read_bytes() == b"transcoded"
    assert video_path.read_bytes() == b"source"


@pytest.mark.parametrize(
    ("flags", "expected_backups"),
    [([], 1), (["--overwrite"], 0)],
    ids=["backup by default", "no backup with --overwrite"],
)
def test_clip_in_place_backs_up_the_source_unless_overwriting(
    tmp_path, video_path, fake_transcode, mock_video_probe, flags, expected_backups
):
    """Verify clipping in place keeps the original as a backup unless --overwrite is set."""
    # Given: A source video clipped in place
    video_path.write_bytes(b"source")
    mock_video_probe("reference.json")

    # When: Clipping
    exit_code = run_vidcleaner(["clip", *flags, str(video_path)])

    # Then: The clip replaces the source, and the original survives only as a backup
    backups = list(tmp_path.glob(f"{video_path.name}.*.bak"))
    assert exit_code == 0
    assert video_path.read_bytes() == b"transcoded"
    assert len(backups) == expected_backups
    assert all(backup.read_bytes() == b"source" for backup in backups)


def test_clip_cleanup_failure_still_reports_the_save(
    mocker, video_path, capsys, mock_ffmpeg, mock_video_probe, stub_output_write
):
    """Verify a cleanup error after a successful clip is a warning, not a crash.

    The clip is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or propagate out of main().
    """
    # Given: a clip that writes successfully but whose temp-directory housekeeping raises
    args = ["clip", str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clip
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The save survives, the cleanup failure is a warning, and the run still succeeds
    assert exit_code == 0
    assert "✔ Saved to" in output
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
    mocker, mock_ffprobe_box, video_path, capsys, mock_ffmpeg, tmp_path, stub_output_write
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

    # When: Running clip over both
    exit_code = run_vidcleaner(args)

    captured = capsys.readouterr()

    # Then: The good file was still clipped, and the run reports the failure
    mock_ffmpeg.assert_called_once()
    assert exit_code == 1
    assert NO_VIDEO_STREAMS_REASON in captured.out + captured.err
    assert "✔ Saved to" in captured.out


def test_clip_failure_clears_its_temp_files_and_continues(
    tmp_path, mocker, mock_video_probe, fake_transcode
):
    """Verify a failed clip discards its temp files and the next file is still clipped."""
    # Given: Two videos where ffmpeg fails on the first
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    for video in (first, second):
        video.write_bytes(b"source")
    mock_video_probe("reference.json")
    real_step = clip_video.run_ffmpeg_step

    def fail_first(video, command, **kwargs):
        if video.path.name == first.name:
            msg = "ffmpeg exited 1"
            raise RuntimeError(msg)
        return real_step(video, command, **kwargs)

    mocker.patch("vid_cleaner.cli.clip_video.run_ffmpeg_step", side_effect=fail_first)
    cleanup = mocker.spy(TempFile, "clean_up")

    # When: Clipping both
    exit_code = run_vidcleaner(["clip", "--overwrite", str(first), str(second)])

    # Then: The run fails, both files' temp files are cleared, and the second is clipped
    assert exit_code == 1
    assert cleanup.call_count == 2
    assert first.read_bytes() == b"source"
    assert second.read_bytes() == b"transcoded"
