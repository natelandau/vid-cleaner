"""Test where the clean subcommand writes its results and what happens to the originals."""

from pathlib import Path

import pytest
from iso639 import Lang

from tests.helpers import run_vidcleaner

from vid_cleaner.models.video_file import VideoFile  # isort: skip
from vid_cleaner.controllers import TempFile  # isort: skip


def test_clean_multiple_files_use_distinct_output_paths(
    mocker, mock_ffmpeg, tmp_path, mock_video_probe
) -> None:
    """Verify each input file is written to its own output path, not the first file's."""
    # Given: two distinct input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()

    args = ["clean", "-vv", "--downmix", str(first), str(second)]

    # And: mocked external dependencies; copy_file echoes the destination it was handed
    mock_video_probe("reference.json")
    mock_copy = mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: cleaning both files in a single invocation
    exit_code = run_vidcleaner(args)

    # Then: each file is copied to its own destination, not both to the first file's path
    assert exit_code == 0
    destinations = [call.args[1] for call in mock_copy.mock_calls]
    assert destinations == [first.resolve(), second.resolve()]


def test_clean_multiple_files_overwrite_each_in_place(
    mocker, mock_ffmpeg, tmp_path, mock_video_probe
) -> None:
    """Verify each file overwrites itself with --overwrite, leaving no original deleted."""
    # Given: two distinct input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()

    args = ["clean", "-vv", "--overwrite", "--downmix", str(first), str(second)]

    # And: mocked external dependencies; copy_file echoes the destination it was handed
    mock_video_probe("reference.json")
    mock_copy = mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: cleaning both files in place
    exit_code = run_vidcleaner(args)

    # Then: each file overwrites itself and neither original is deleted
    assert exit_code == 0
    destinations = [call.args[1] for call in mock_copy.mock_calls]
    assert destinations == [first.resolve(), second.resolve()]
    assert first.exists()
    assert second.exists()


def test_clean_write_output_cleanup_failure_still_reports_the_save(
    mocker, video_path, capsys, mock_ffmpeg, mock_video_probe
):
    """Verify a cleanup error after a successful write is a warning, not a per-file failure.

    The file is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or count the file as failed.
    """
    # Given: a file that cleans and writes successfully, but whose temp-directory
    # housekeeping raises after the output is already safely in place
    args = ["clean", "-vv", str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: the run still exits successfully, the save is still reported, and the
    # cleanup failure is surfaced as a warning rather than a batch failure
    assert exit_code == 0
    assert "cleaned_video.mkv" in output
    assert "temp dir busy" in output
    assert "failed" not in output


def test_clean_removes_the_original_even_when_temp_cleanup_fails(
    mocker, mock_ffmpeg, capsys, tmp_path, mock_video_probe
):
    """Verify a failed temp cleanup does not skip removing the original under --overwrite."""
    # Given: --overwrite renaming the result to a new container, and temp housekeeping
    # that raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The original is gone and the cleanup failure is reported as its own warning
    assert exit_code == 0
    assert not source.exists()
    assert "temp dir busy" in output


def test_clean_reports_a_failed_original_removal_accurately(
    mocker, mock_ffmpeg, capsys, tmp_path, mock_video_probe
):
    """Verify a failed removal of the original is not reported as a temp-file cleanup error."""
    # Given: --overwrite renaming the result to a new container, where removing the
    # original raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(Path, "unlink", side_effect=OSError("read-only file system"))

    # When: Running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The warning names the original file, not the temporary files
    assert exit_code == 0
    assert "could not remove the original" in output
    assert "could not clean up temporary files" not in output


def test_clean_out_with_overwrite_leaves_the_input_alone(
    mocker, mock_ffmpeg, tmp_path, mock_video_probe
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
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The input file is still there
    assert exit_code == 0
    assert source.exists()


def test_clean_from_depth_writes_each_file_back_to_its_own_directory(
    video_library, mocker, mock_ffmpeg, monkeypatch, tmp_path
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

    write = mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    args = ["clean", "--overwrite", "--from", str(directory), "--depth", "1", "--yes"]

    # When: Cleaning everything discovered one level deep
    exit_code = run_vidcleaner(args)

    # Then: Each file is written back over itself, with no backup kept
    assert exit_code == 0
    assert sorted(call.args[1] for call in write.mock_calls) == sorted(
        [
            directory / "top.mkv",
            directory / "movie_a" / "a.mkv",
            directory / "movie_b" / "b.mkv",
        ]
    )
    assert {call.kwargs["overwrite"] for call in write.mock_calls} == {True}


@pytest.fixture
def fake_transcode(mocker):
    """Replace the ffmpeg pass with one that writes a real temporary file.

    Lets a test exercise the genuine `copy_to_output` write, and so the output path and
    the fate of the original, rather than stubbing the write out.

    Returns:
        Callable[[], None]: Call to install the replacement.
    """

    def _inner() -> None:
        def run_ffmpeg(command, *, on_progress):
            command.output_path.write_bytes(b"transcoded")

        mocker.patch("vid_cleaner.cli.ffmpeg_step.run_ffmpeg", run_ffmpeg)

    return _inner


def test_clean_vp9_writes_a_webm_file(mocker, tmp_path, fake_transcode, mock_video_probe):
    """Verify --vp9 names the output for the container it actually produced."""
    # Given: An .mkv input converted to VP9
    video = tmp_path / "movie.mkv"
    video.touch()
    mock_video_probe("reference.json")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    fake_transcode()

    # When: Cleaning it to VP9 without --overwrite
    args = ["clean", "--vp9", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: WebM data lands in a .webm file, the original is untouched, and no pointless
    # backup of a file that was never overwritten is left behind
    assert exit_code == 0
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert video.exists()
    assert not list(tmp_path.glob("*.bak"))


def test_clean_vp9_overwrite_removes_the_original_container(
    mocker, tmp_path, fake_transcode, mock_video_probe
):
    """Verify --overwrite removes the source .mkv once the result lands in a .webm."""
    # Given: An .mkv input converted to VP9 with --overwrite
    video = tmp_path / "movie.mkv"
    video.touch()
    mock_video_probe("reference.json")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    fake_transcode()

    # When: Cleaning it to VP9 in place
    args = ["clean", "--vp9", "--overwrite", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: Only the .webm remains, so the container change does not leave two copies
    assert exit_code == 0
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert not video.exists()


def test_clean_dryrun_writes_nothing_to_the_cache(
    mocker, video_path, capsys, mock_ffmpeg, monkeypatch, tmp_path, mock_video_probe
):
    """Verify a dry run previews the ffmpeg command without creating anything under cache_dir."""
    # Given: A cache directory inside a parent no one can write to
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setenv("VIDCLEANER_CACHE_DIR", str(locked / "cache"))
    mock_video_probe("reference.json")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    locked.chmod(0o500)

    # When: Dry-running a clean that would change the file
    try:
        exit_code = run_vidcleaner(["-n", "clean", str(video_path)])
    finally:
        locked.chmod(0o700)

    # Then: The command is previewed, ffmpeg never runs, and nothing was created
    assert exit_code == 0
    assert "Processing (dry run)" in capsys.readouterr().out
    mock_ffmpeg.assert_not_called()
    assert not any(locked.iterdir())
