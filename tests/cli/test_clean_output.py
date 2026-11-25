"""Test where the clean subcommand writes its results and what happens to the originals."""

import math
from pathlib import Path

import cappa
import pytest
from cappa.base import invoke

from tests.helpers import leftover_cache_files, run_vidcleaner
from vid_cleaner.cli.clean_video import (
    NOT_ENOUGH_SPACE_ERROR,
    ORIGINAL_REMOVAL_WARNING,
    OUTPUT_SIZE_MARGIN,
)
from vid_cleaner.cli.ffmpeg_step import (
    DRY_RUN_HEADER_SUFFIX,
    RESULT_KEPT_MESSAGE,
    VERIFY_INTERRUPTED_MESSAGE,
)
from vid_cleaner.constants import SYMBOL_CHECK
from vid_cleaner.controllers.validation import OUTPUT_STREAM_COUNT_REASON
from vid_cleaner.utils.cli import SAVED_TO_MESSAGE, TEMP_CLEANUP_WARNING
from vid_cleaner.vidcleaner import VidCleaner, configure_output

from vid_cleaner.controllers import TempFile  # isort: skip


@pytest.mark.parametrize("flags", [[], ["--overwrite"]], ids=["backup", "overwrite"])
def test_clean_multiple_files_write_each_to_its_own_path(
    mock_ffmpeg, tmp_path, mock_video_probe, mock_original_language, stub_output_write, flags
) -> None:
    """Verify each input file is written back to its own path, not the first file's."""
    # Given: two distinct input files
    first = tmp_path / "first_video.mkv"
    first.touch()
    second = tmp_path / "second_video.mkv"
    second.touch()
    mock_video_probe("reference.json")

    # When: cleaning both files in a single invocation
    exit_code = run_vidcleaner(["clean", *flags, "--downmix", str(first), str(second)])

    # Then: each file is written to its own path and neither original is deleted
    assert exit_code == 0
    destinations = [call.kwargs["dst"] for call in stub_output_write.mock_calls]
    assert destinations == [first.resolve(), second.resolve()]
    assert first.exists()
    assert second.exists()


def test_clean_write_output_cleanup_failure_still_reports_the_save(
    mocker,
    video_path,
    capsys,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify a cleanup error after a successful write is a warning, not a per-file failure.

    The file is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or count the file as failed.
    """
    # Given: a file that cleans and writes successfully, but whose temp-directory
    # housekeeping raises after the output is already safely in place
    args = ["clean", "-vv", str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: the run still exits successfully, the save is still reported, and the
    # cleanup failure is surfaced as a warning rather than a batch failure
    assert exit_code == 0
    assert f"{SYMBOL_CHECK} {SAVED_TO_MESSAGE}" in output
    assert "temp dir busy" in output
    assert "failed" not in output


def test_clean_removes_the_original_even_when_temp_cleanup_fails(
    mocker,
    mock_ffmpeg,
    capsys,
    tmp_path,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify a failed temp cleanup does not skip removing the original under --overwrite."""
    # Given: --overwrite renaming the result to a new container, and temp housekeeping
    # that raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mock_video_probe("reference.json")
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The original is gone and the cleanup failure is reported as its own warning
    assert exit_code == 0
    assert not source.exists()
    assert "temp dir busy" in output


def test_clean_reports_a_failed_original_removal_accurately(
    mocker,
    mock_ffmpeg,
    capsys,
    tmp_path,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify a failed removal of the original is not reported as a temp-file cleanup error."""
    # Given: --overwrite renaming the result to a new container, where removing the
    # original raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mock_video_probe("reference.json")
    mocker.patch.object(Path, "unlink", side_effect=OSError("read-only file system"))

    # When: Running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The warning names the original file, not the temporary files
    assert exit_code == 0
    assert ORIGINAL_REMOVAL_WARNING in output
    assert TEMP_CLEANUP_WARNING not in output


def test_clean_out_with_overwrite_leaves_the_input_alone(
    mock_ffmpeg, tmp_path, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify `--out` never deletes the input, even when `--overwrite` is also passed.

    `--overwrite` only says "do not keep a backup of what I am replacing". With `--out`
    the input is not being replaced, so removing it would destroy a file the user asked
    to read from, not write to.
    """
    # Given: --overwrite writing the result to a destination the user chose
    source = tmp_path / "movie.mkv"
    source.touch()
    destination = tmp_path / "out.mkv"
    args = ["clean", "--overwrite", "--out", str(destination), str(source)]
    mock_video_probe("reference.json")

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The input file is still there
    assert exit_code == 0
    assert source.exists()


def test_clean_from_depth_writes_each_file_back_to_its_own_directory(
    video_library, mock_ffmpeg, monkeypatch, tmp_path, mock_original_language, stub_output_write
):
    """Verify a recursive --from run rewrites each file in place, not into one directory.

    A library holds each film in its own subdirectory, so a destination derived from the
    `--from` root or the working directory would collapse the whole selection onto one
    path and destroy every file but the last.
    """
    # Given: A library with files at the root and one level down, and a working directory
    # unrelated to any of them
    directory = video_library(
        [
            ("top.mkv", 100, 1_000_000),
            ("movie_a/a.mkv", 100, 1_000_000),
            ("movie_b/b.mkv", 100, 1_000_000),
        ]
    )
    elsewhere = tmp_path / "unrelated_cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    write = stub_output_write
    args = ["clean", "--overwrite", "--from", str(directory), "--depth", "1", "--yes"]

    # When: Cleaning everything discovered one level deep
    exit_code = run_vidcleaner(args)

    # Then: Each file is written back over itself, with no backup kept
    assert exit_code == 0
    assert sorted(call.kwargs["dst"] for call in write.mock_calls) == sorted(
        [
            directory / "top.mkv",
            directory / "movie_a" / "a.mkv",
            directory / "movie_b" / "b.mkv",
        ]
    )
    assert {call.kwargs["overwrite"] for call in write.mock_calls} == {True}


def test_clean_vp9_writes_a_webm_file(
    capsys, tmp_path, fake_transcode, mock_video_probe, mock_original_language
):
    """Verify --vp9 writes and announces the output for the container it actually produced."""
    # Given: An .mkv input converted to VP9
    video = tmp_path / "movie.mkv"
    video.touch()
    mock_video_probe("reference.json")

    # When: Cleaning it to VP9 without --overwrite
    args = ["clean", "--vp9", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: WebM data lands in a .webm file the user is told about, the original is
    # untouched, and no pointless backup of a file that was never overwritten is left behind
    assert exit_code == 0
    assert "`movie.webm`" in capsys.readouterr().out
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert video.exists()
    assert not list(tmp_path.glob("*.bak"))


def test_clean_vp9_overwrite_removes_the_original_container(
    tmp_path, fake_transcode, mock_video_probe, mock_original_language
):
    """Verify --overwrite removes the source .mkv once the result lands in a .webm."""
    # Given: An .mkv input converted to VP9 with --overwrite
    video = tmp_path / "movie.mkv"
    video.touch()
    mock_video_probe("reference.json")

    # When: Cleaning it to VP9 in place
    args = ["clean", "--vp9", "--overwrite", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: Only the .webm remains, so the container change does not leave two copies
    assert exit_code == 0
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert not video.exists()


def test_clean_keeps_the_result_when_the_copy_fails(
    mocker, capsys, tmp_path, video_path, fake_transcode, mock_video_probe, mock_original_language
):
    """Verify a failed copy to the destination keeps the transcode and tells the user where."""
    # Given: A destination that runs out of space during the copy
    video_path.write_bytes(b"source")
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.utils.cli.copy_file", side_effect=OSError(28, "No space left on device")
    )

    # When: Cleaning the file
    exit_code = run_vidcleaner(["clean", "--h265", str(video_path)])

    # Then: The file fails, the original is untouched, and the transcode stays in the cache
    output = capsys.readouterr()
    kept = leftover_cache_files(tmp_path / "cache")
    assert exit_code == 1
    assert video_path.read_bytes() == b"source"
    assert [path.read_bytes() for path in kept] == [b"transcoded"]
    assert RESULT_KEPT_MESSAGE in output.out + output.err


@pytest.mark.parametrize(
    ("shortfall_bytes", "expected_exit"),
    [(0, 0), (1, 1)],
    ids=["room-for-both-copies", "one-byte-short"],
)
def test_clean_stops_before_encoding_without_free_space(
    mocker,
    capsys,
    video_path,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    shortfall_bytes,
    expected_exit,
):
    """Verify a disk holding both the cache and the destination needs room for two copies."""
    # Given: A 10-byte source whose cache and destination share one filesystem
    video_path.write_bytes(b"0123456789")
    room_for_both_copies = 2 * math.ceil(10 * OUTPUT_SIZE_MARGIN)
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clean_video.shutil.disk_usage",
        return_value=mocker.MagicMock(free=room_for_both_copies - shortfall_bytes),
    )

    # When: Cleaning the file
    exit_code = run_vidcleaner(["clean", "--h265", str(video_path)])

    # Then: The encode runs only when the disk fits the cache copy and the staged copy
    output = capsys.readouterr()
    assert exit_code == expected_exit
    assert mock_ffmpeg.called is (expected_exit == 0)
    assert (NOT_ENOUGH_SPACE_ERROR in output.out + output.err) is (expected_exit == 1)
    assert video_path.read_bytes() == b"0123456789"


def test_clean_dryrun_writes_nothing_to_the_cache(
    video_path, capsys, mock_ffmpeg, monkeypatch, tmp_path, mock_video_probe, mock_original_language
):
    """Verify a dry run previews the ffmpeg command without creating anything under cache_dir."""
    # Given: A cache directory inside a parent no one can write to
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setenv("VIDCLEANER_CACHE_DIR", str(locked / "cache"))
    mock_video_probe("reference.json")
    locked.chmod(0o500)

    # When: Dry-running a clean that would change the file
    try:
        exit_code = run_vidcleaner(["-n", "clean", str(video_path)])
    finally:
        locked.chmod(0o700)

    # Then: The command is previewed, ffmpeg never runs, and nothing was created
    assert exit_code == 0
    assert DRY_RUN_HEADER_SUFFIX in capsys.readouterr().out
    mock_ffmpeg.assert_not_called()
    assert not any(locked.iterdir())


def test_clean_verify_failure_keeps_original(
    tmp_path, capsys, fake_transcode, mock_video_probe, mock_original_language
) -> None:
    """Verify an output that misses the plan fails the file and leaves the original alone."""
    # Given: a plan that drops audio streams, while the output probe still shows every stream
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"original")
    cache_dir = tmp_path / "cache"
    mock_video_probe("reference.json")

    # When: cleaning with a quick check
    exit_code = run_vidcleaner(["clean", "--verify=quick", "--langs=en", str(video)])

    # Then: the file fails, the input is unchanged, and nothing is left behind
    assert exit_code == 1
    assert OUTPUT_STREAM_COUNT_REASON in capsys.readouterr().out
    assert video.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.bak"))
    assert not leftover_cache_files(cache_dir)


def test_clean_verify_skipped_on_dry_run(
    mocker, video_path, mock_video_probe, mock_original_language
) -> None:
    """Verify a dry run never decodes, because it writes no output."""
    # Given: a decode check that would be seen if called
    mock_video_probe("reference.json")
    decode = mocker.patch("vid_cleaner.controllers.validation.decode_check", return_value=None)

    # When: dry-running with the full check
    exit_code = run_vidcleaner(["clean", "-n", "--verify=full", str(video_path)])

    # Then: nothing was decoded
    assert exit_code == 0
    decode.assert_not_called()


def test_clean_verify_interrupt_exits(
    mocker,
    tmp_path,
    video_path,
    fake_transcode,
    mock_video_probe,
    mock_original_language,
) -> None:
    """Verify Ctrl-C during the decode check stops the run and removes the temp file."""
    # Given: an output that passes the probe checks, then a decode that is interrupted
    cache_dir = tmp_path / "cache"
    mock_video_probe("reference.json")
    mocker.patch("vid_cleaner.controllers.validation.decode_check", side_effect=KeyboardInterrupt)
    args = [
        "clean",
        "--verify=full",
        "--keep-subs",
        "--keep-commentary",
        "--h265",
        "--langs=en,fr,da",
        str(video_path),
    ]

    # When: running clean
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: the run stops with code 1 and leaves no temp file
    assert exc_info.value.code == 1
    assert exc_info.value.message == VERIFY_INTERRUPTED_MESSAGE
    assert not leftover_cache_files(cache_dir)


def test_clean_verify_skipped_when_plan_changes_nothing(
    mocker, video_path, mock_ffmpeg, mock_video_probe, mock_original_language
) -> None:
    """Verify a clean that leaves the file as is never runs the verifier."""
    # Given: keep-everything options that make the plan a no-op, and a spy on the verifier
    mock_video_probe("reference.json")
    verifier = mocker.patch("vid_cleaner.cli.ffmpeg_step.verify_output", autospec=True)
    args = [
        "clean",
        "--verify=full",
        "--keep-subs",
        "--keep-commentary",
        "--langs=en,fr,da",
        str(video_path),
    ]

    # When: running clean
    exit_code = run_vidcleaner(args)

    # Then: ffmpeg and the verifier never ran
    assert exit_code == 0
    mock_ffmpeg.assert_not_called()
    verifier.assert_not_called()
