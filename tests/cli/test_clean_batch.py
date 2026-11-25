"""Test how the clean subcommand runs a batch of files: discovery, failures, and interrupts."""

import cappa
import pytest
from iso639 import Lang

from tests.helpers import run_vidcleaner
from vid_cleaner.exceptions import VideoCleanError
from vid_cleaner.models import video_file as video_file_module
from vid_cleaner.models.conversion_plan import ConversionPlan

from vid_cleaner.models.video_file import VideoFile  # isort: skip
from vid_cleaner.controllers import TempFile  # isort: skip


def test_clean_interrupt_removes_temp_files_and_stops(
    mocker, mock_ffmpeg, video_path, tmp_path, mock_video_probe
) -> None:
    """Verify Ctrl-C during ffmpeg removes the run's temporary files and exits with an error."""
    # Given: a file that needs cleaning and an ffmpeg run the user interrupts
    mock_video_probe("reference.json")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mock_ffmpeg.return_value.run_command_with_progress.side_effect = KeyboardInterrupt

    # When: running the clean command
    exit_code = run_vidcleaner(["clean", str(video_path)])

    # Then: the run stops with an error, leaves no temporary files, and keeps the original
    assert exit_code == 1
    cache_dir = tmp_path / "cache"
    assert not cache_dir.exists() or not any(cache_dir.rglob("*"))
    assert video_path.exists()


def test_clean_from_previews_and_requires_confirmation(capsys, video_library):
    """Verify discovery mode renders the selection table and refuses to run non-interactively."""
    # Given: Two discoverable files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    args = ["clean", "--from", str(directory), "--filters", "h264"]

    # When: Running clean with no TTY and no --yes
    exit_code = run_vidcleaner(args)

    output, error = capsys.readouterr()

    # Then: The table rendered before the refusal, so the user still sees the selection
    assert exit_code == 1
    assert "apple.mkv" in output
    assert "--yes" in error


def test_clean_from_dryrun_skips_the_prompt(capsys, video_library, mock_ffmpeg):
    """Verify --dryrun previews without prompting, since a preview is not an action."""
    # Given: Two discoverable files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    args = ["-n", "clean", "--from", str(directory), "--filters", "h264"]

    # When: Running a dry run with no TTY and no --yes
    exit_code = run_vidcleaner(args)

    output, error = capsys.readouterr()

    # Then: It completes successfully without demanding --yes
    assert exit_code == 0
    assert "apple.mkv" in output
    assert "--yes" not in error


def test_clean_from_limit_selects_top_results(capsys, video_library, mock_ffmpeg):
    """Verify --limit narrows what discovery mode acts on."""
    # Given: Three discoverable files
    directory = video_library(
        [
            ("apple.mkv", 100, 1_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 1_000_000),
        ]
    )
    args = ["-n", "clean", "--from", str(directory), "--sort", "size", "--limit", "1"]

    # When: Previewing the single largest file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: Only the largest file is selected
    assert exit_code == 0
    assert "banana.mkv" in output
    assert "apple.mkv" not in output


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(
            lambda path: VideoCleanError(path=path, reason="no video streams found"),
            id="unusable video",
        ),
        pytest.param(lambda path: OSError(28, "No space left on device"), id="disk error"),
        pytest.param(lambda path: RuntimeError("ffmpeg exited 1"), id="ffmpeg failure"),
    ],
)
def test_clean_continues_after_a_file_fails(capsys, tmp_path, mocker, make_error):
    """Verify one failing file does not discard the work queued behind it."""
    # Given: Two files where the first fails to clean
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def plan_clean(self, config, options):
        if self.path.name == "first.mkv":
            raise make_error(self.path)
        return ConversionPlan()

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", plan_clean)
    mocker.patch.object(VideoFile, "plan_changes_file", return_value=False)

    # When: Cleaning both files
    exit_code = run_vidcleaner(["-n", "clean", str(first), str(second)])

    output, error = capsys.readouterr()

    # Then: The second file was still attempted and the failure is reported at the end
    assert exit_code == 1
    assert "second.mkv" in output
    assert "first.mkv" in output + error
    assert "1 file(s) failed" in output + error


def test_clean_exits_zero_when_every_file_succeeds(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify a clean run with no failures still exits successfully."""
    # Given: One file that cleans without error
    video = tmp_path / "movie.mkv"
    video.touch()
    mocker.patch.object(VideoFile, "plan_clean", return_value=ConversionPlan())
    mocker.patch.object(VideoFile, "plan_changes_file", return_value=False)

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: The command exits successfully with no failure report
    assert exit_code == 0
    assert "failed" not in capsys.readouterr().out


def test_clean_keyboard_interrupt_aborts_the_whole_batch(capsys, tmp_path, mocker):
    """Verify Ctrl-C stops the run rather than being collected as a per-file failure."""
    # Given: Two files where the first raises the exit cappa uses for an interrupt
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def plan_clean(self, config, options):
        if self.path.name == "first.mkv":
            raise cappa.Exit(code=1)
        return ConversionPlan()

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", plan_clean)
    mocker.patch.object(VideoFile, "plan_changes_file", return_value=False)

    # When: Cleaning both files
    args = ["-n", "clean", str(first), str(second)]
    exit_code = run_vidcleaner(args)

    # Then: The run aborts before reaching the second file
    assert exit_code == 1
    assert "second.mkv" not in capsys.readouterr().out


def test_clean_from_empty_directory_still_exits_zero(capsys, tmp_path):
    """Verify a real but empty directory remains a successful no-op, not a failure."""
    # Given: An existing directory holding no video files
    directory = tmp_path / "library"
    directory.mkdir()
    args = ["clean", "--from", str(directory)]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command reports the empty directory and exits successfully
    assert exit_code == 0
    assert "No video files found" in capsys.readouterr().err


def _normalize(text: str) -> str:
    """Collapse Rich's line wrapping so assertions can match whole phrases.

    Returns:
        str: The text with every run of whitespace reduced to a single space.
    """
    return " ".join(text.split())


def test_clean_failure_lines_name_the_file_exactly_once(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify a domain error's failure line carries the short reason, not the full message.

    `VideoCleanError` embeds the absolute path in its own message, so reporting `str(e)`
    under a line already prefixed with the filename names the file twice.
    """
    # Given: One file failing with a domain error and one with a plain RuntimeError
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def plan_clean(self, config, options):
        if self.path.name == "first.mkv":
            raise VideoCleanError(path=self.path, reason="no video streams found")
        msg = "boom"
        raise RuntimeError(msg)

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", plan_clean)

    # When: Cleaning both files
    args = ["-n", "clean", str(first), str(second)]
    exit_code = run_vidcleaner(args)

    output, error = capsys.readouterr()
    combined = _normalize(output + error)

    # Then: The domain failure names the file once, and the plain error still names it
    assert exit_code == 1
    assert "first.mkv: no video streams found" in combined
    assert "Could not clean" not in combined
    assert "second.mkv: boom" in combined


def test_clean_clears_the_temp_directory_when_a_file_fails(tmp_path, mocker, mock_ffmpeg):
    """Verify a failed file's full-size temp transcode is discarded immediately.

    A batch that fails on every file would otherwise hold one full-size intermediate per
    file on disk until the process exits.
    """
    # Given: A file that fails to clean
    video = tmp_path / "movie.mkv"
    video.touch()

    def plan_clean(self, config, options):
        raise VideoCleanError(path=self.path, reason="no video streams found")

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", plan_clean)
    cleanup = mocker.patch.object(TempFile, "clean_up")

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    exit_code = run_vidcleaner(args)

    # Then: The temp directory was cleared as part of handling the failure
    assert exit_code == 1
    cleanup.assert_called_once()


def test_clean_from_cleanup_failure_does_not_mask_the_real_failure(
    capsys, tmp_path, mocker, mock_ffmpeg
):
    """Verify a cleanup error while handling a failure does not replace the reported cause."""
    # Given: A file that fails to clean and whose temp cleanup then also fails
    video = tmp_path / "movie.mkv"
    video.touch()

    def plan_clean(self, config, options):
        raise VideoCleanError(path=self.path, reason="no video streams found")

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", plan_clean)
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    exit_code = run_vidcleaner(args)

    output, error = capsys.readouterr()
    combined = _normalize(output + error)

    # Then: The original cause is still what the batch reports
    assert exit_code == 1
    assert "movie.mkv: no video streams found" in combined


def test_clean_from_yes_reaches_the_transcode_loop(capsys, video_library, mocker, mock_ffmpeg):
    """Verify --yes skips the prompt and actually transcodes the discovered selection."""
    # Given: Two discoverable files and a stubbed writer
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    args = ["clean", "--from", str(directory), "--filters", "h264", "--yes"]

    # When: Running clean with --yes and no TTY
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: Both files were transcoded and written
    assert exit_code == 0
    assert mock_ffmpeg.call_count == 2
    assert "apple.mkv" in output
    assert "banana.mkv" in output


def test_clean_from_applies_conversion_flags_to_each_selected_file(
    video_library, mocker, mock_ffmpeg
):
    """Verify a conversion flag such as --h265 reaches files that discovery selected."""
    # Given: One discoverable h264 file and a stubbed writer
    directory = video_library([("apple.mkv", 100_000_000, 1_000_000)])
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Cleaning the directory with --h265
    exit_code = run_vidcleaner(["clean", "--from", str(directory), "--h265", "--yes"])

    # Then: The single ffmpeg pass encodes the video to H.265
    assert exit_code == 0
    mock_ffmpeg.assert_called_once()
    command = " ".join(mock_ffmpeg.call_args.args[0])
    assert "libx265" in command


def test_clean_from_yes_reuses_the_discovery_probe(video_library, mocker, mock_ffmpeg):
    """Verify each file is probed once, not again when the transcode loop reaches it.

    Discovery already probed every candidate and `VideoFile` caches the result, so
    `select_video_files` hands back those instances. Rebuilding them from paths would
    silently double the dominant cost of a large `--from` run.
    """
    # Given: Two discoverable files and a stubbed writer
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    # Wrap the fixture's stub in a fresh mock so the call count is typed and local to this test
    probe = mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=video_file_module.get_probe_as_box,
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    args = ["clean", "--from", str(directory), "--filters", "h264", "--yes"]

    # When: Running clean with --yes
    exit_code = run_vidcleaner(args)

    # Then: ffprobe ran exactly once per file
    assert exit_code == 0
    assert probe.call_count == 2


def test_clean_from_declining_the_prompt_changes_nothing(
    video_library, mocker, mock_ffmpeg, interactive_console
):
    """Verify answering no is a successful no-op that leaves every file untouched."""
    # Given: A discoverable file and a user who declines the prompt
    directory = video_library([("apple.mkv", 100, 1_000_000)])
    video = directory / "apple.mkv"
    original = video.read_bytes()
    interactive_console(is_terminal=True)
    mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=False)
    write = mocker.patch("vid_cleaner.cli.clean_video.copy_to_output")
    args = ["clean", "--from", str(directory), "--filters", "h264"]

    # When: Running clean and declining
    exit_code = run_vidcleaner(args)

    # Then: Nothing was transcoded, nothing was written, and the file is unchanged
    assert exit_code == 0
    mock_ffmpeg.assert_not_called()
    write.assert_not_called()
    assert video.read_bytes() == original
