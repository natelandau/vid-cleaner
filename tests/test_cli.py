"""Test the vidcleaner command line interface."""

import stat
from pathlib import Path

import cappa
import pytest
from cappa.base import invoke

from vid_cleaner import constants
from vid_cleaner.config import Config, RunOptions
from vid_cleaner.models.conversion_plan import ConversionPlan
from vid_cleaner.utils import copy_to_output, create_default_config
from vid_cleaner.vidcleaner import VidCleaner, configure_output, verify_required_binaries


@pytest.mark.parametrize(
    ("subcommand"),
    [("inspect"), ("clip"), ("clean"), ("cache")],
)
def test_vidcleaner_cli_help(capsys, subcommand: str) -> None:
    """Verify help text displays for each subcommand."""
    # Given: Command line arguments requesting help
    args = [subcommand, "--help"] if subcommand else ["--help"]

    # When: Invoking CLI with help flag
    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: Help output contains expected information
    output = capsys.readouterr().out
    assert "Usage: vidcleaner" in output
    assert "--help" in output
    assert " [-v]" in output


def test_copy_to_output_backs_up_existing(tmp_path: Path) -> None:
    """Verify writing over an existing file backs it up and reports both actions."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying to the destination without overwrite
    out_file, messages = copy_to_output(src, dst, overwrite=False)

    # Then: the original is preserved as a backup and the new content is written
    backups = list(dst.resolve().parent.glob("movie.mkv.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original content"
    assert out_file.read_text() == "new content"

    # And: both the backup and the save are reported
    assert any("Backed up original to" in m for m in messages)
    assert any("Saved to" in m and "movie.mkv" in m for m in messages)


def test_copy_to_output_overwrite_skips_backup(tmp_path: Path) -> None:
    """Verify overwrite mode replaces the destination without keeping a backup."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying with overwrite enabled
    out_file, messages = copy_to_output(src, dst, overwrite=True)

    # Then: no backup is created and only the save is reported
    assert list(dst.resolve().parent.glob("*.bak")) == []
    assert out_file.read_text() == "new content"
    assert all("Backed up" not in m for m in messages)
    assert any("Saved to" in m for m in messages)


def test_copy_to_output_preserves_original_when_staging_fails(tmp_path: Path, mocker) -> None:
    """Verify a staging failure leaves the original intact with no backup or temp file."""
    # Given: an existing destination and a source, with the staging copy set to fail
    #        after partially writing (mimicking a real mid-copy I/O error)
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    def _fail_mid_copy(*, src, dst, **kwargs):
        Path(dst).write_text("corrupt partial")
        raise OSError(5, "Input/output error")

    mocker.patch("vid_cleaner.utils.cli.copy_file", autospec=True, side_effect=_fail_mid_copy)

    # When: the staging copy fails
    with pytest.raises(OSError, match="Input/output error"):
        copy_to_output(src, dst, overwrite=True)

    # Then: the original is byte-for-byte intact and nothing is left behind
    assert dst.read_text() == "original content"
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob(".*vidcleaner-tmp*")) == []


@pytest.mark.parametrize("overwrite", [True, False])
def test_copy_to_output_leaves_no_temp_file_on_success(tmp_path: Path, *, overwrite: bool) -> None:
    """Verify a successful write leaves no staging temp file behind in either mode."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying to the destination
    copy_to_output(src, dst, overwrite=overwrite)

    # Then: the content is written and no staging temp remains
    assert dst.read_text() == "new content"
    assert list(tmp_path.glob(".*vidcleaner-tmp*")) == []


@pytest.mark.parametrize("overwrite", [True, False])
def test_copy_to_output_creates_new_destination(tmp_path: Path, *, overwrite: bool) -> None:
    """Verify copying to a non-existent destination creates it in both modes."""
    # Given: a processed source and a destination that does not exist yet
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"

    # When: copying to the missing destination
    out_file, messages = copy_to_output(src, dst, overwrite=overwrite)

    # Then: the destination is created with no backup and the save is reported
    assert out_file.read_text() == "new content"
    assert list(tmp_path.glob("*.bak")) == []
    assert any("Saved to" in m for m in messages)


def test_copy_to_output_restores_original_when_swap_fails(tmp_path: Path, mocker) -> None:
    """Verify a failed final swap restores the original to its path instead of stranding it."""
    # Given: an existing destination and source, with the atomic swap (but not the backup
    #        move) set to fail, mimicking a rename failure after the original was moved aside
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    real_replace = Path.replace

    def _fail_swap(self: Path, target: Path) -> Path:
        # Only the staged temp -> dst swap fails; the dst -> .bak move and the
        # .bak -> dst restore both run for real.
        if "vidcleaner-tmp" in self.name:
            raise OSError(5, "Input/output error")
        return real_replace(self, target)

    mocker.patch.object(Path, "replace", autospec=True, side_effect=_fail_swap)

    # When: the swap fails after the backup move
    with pytest.raises(OSError, match="Input/output error"):
        copy_to_output(src, dst, overwrite=False)

    # Then: the original is back at its expected path, with nothing stranded
    assert dst.read_text() == "original content"
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob(".*vidcleaner-tmp*")) == []


def test_copy_to_output_overwrite_does_not_mutate_hardlink(tmp_path: Path) -> None:
    """Verify overwriting replaces the inode so existing hardlinks keep the original content."""
    # Given: a destination with a hardlink sharing its inode (as *arr apps / seeding create)
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")
    link = tmp_path / "seed.mkv"
    link.hardlink_to(dst)

    # When: overwriting the destination
    copy_to_output(src, dst, overwrite=True)

    # Then: the destination has the new content but the hardlink keeps the original
    assert dst.read_text() == "new content"
    assert link.read_text() == "original content"


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        pytest.param(["ffmpeg"], "ffmpeg", id="ffmpeg_only"),
        pytest.param(["ffprobe"], "ffprobe", id="ffprobe_only"),
        pytest.param(["ffmpeg", "ffprobe"], "ffmpeg, ffprobe", id="both"),
    ],
)
def test_missing_binaries_stops_the_run(capsys, mocker, missing, expected) -> None:
    """Verify a command that needs ffmpeg refuses to start when a program is absent."""
    # Given: One or both programs are absent from PATH
    mocker.patch(
        "vid_cleaner.utils.cli.which",
        side_effect=lambda cmd: None if cmd in missing else Path(f"/usr/bin/{cmd}"),
    )

    # When: Running a command that needs them
    args = ["check", "movie.mkv"]
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[verify_required_binaries])

    captured = capsys.readouterr()

    # Then: The run stops and names every absent program
    assert exc_info.value.code == 1
    assert expected in captured.err


def test_present_binaries_allow_the_run(mocker) -> None:
    """Verify the preflight is silent when both programs are on PATH."""
    # Given: Both programs are present
    mocker.patch("vid_cleaner.utils.cli.which", return_value=Path("/usr/bin/ffmpeg"))

    # When: Running the preflight for a command that needs them
    args = ["check", "movie.mkv"]
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[verify_required_binaries])

    # Then: The run reaches the command itself rather than stopping at the preflight
    assert exc_info.value.code == 1  # `movie.mkv` does not exist


def test_cache_runs_without_ffmpeg(capsys, mocker, tmp_path) -> None:
    """Verify the cache command works on a machine with no ffmpeg installed.

    The cache holds API responses, so it never touches a video file.
    """
    # Given: Neither program is on PATH
    mocker.patch("vid_cleaner.utils.cli.which", return_value=None)

    # When: Running the cache command
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=["cache"], deps=[verify_required_binaries])

    captured = capsys.readouterr()

    # Then: The preflight does not block it
    assert exc_info.value.code == 0
    assert "ffmpeg" not in captured.err


@pytest.mark.parametrize(
    "args",
    [
        ["search", ".", "--filters", "bogus"],
        ["search", ".", "--filters", "h264,bogus"],
        ["check", "--from", ".", "--filters", "bogus"],
        ["clean", "--from", ".", "--filters", "bogus"],
    ],
)
def test_invalid_filter_is_a_usage_error(capsys, args):
    """Verify a bad `--filters` value fails at parse time like every other discovery flag."""
    # When: Parsing a filter that names no trait
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The usage-error exit code and message name the flag and the bad value
    _, error = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "--filters" in error
    assert "bogus" in error


@pytest.fixture
def captured_clean(mocker):
    """Record the settings `clean` hands to each file instead of running ffmpeg.

    Returns:
        dict[str, object]: Holds the `config` and `options` of the last cleaned file.
    """
    captured: dict[str, object] = {}

    def _record(self, config: Config, options: RunOptions) -> ConversionPlan:
        captured["config"] = config
        captured["options"] = options
        return ConversionPlan()

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", _record)
    return captured


def _run_clean(argv: list[str]) -> str | int | None:
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=["clean", "-n", *argv], deps=[configure_output])
    return exc_info.value.code


@pytest.mark.parametrize(
    ("file_value", "flags", "expected"),
    [
        ("true", ["--no-keep-commentary"], False),
        ("false", ["--keep-commentary"], True),
        ("false", ["-c"], True),
        ("true", [], True),
    ],
)
def test_track_flag_wins_over_config(
    captured_clean, user_config, mock_video_path, file_value, flags, expected
) -> None:
    """Verify a track flag means what it says whatever the config file holds."""
    user_config(f"keep_commentary = {file_value}")

    code = _run_clean([*flags, str(mock_video_path)])

    assert code == 0
    assert captured_clean["config"].keep_commentary is expected


def test_bundled_short_flags_set_each_setting(captured_clean, mock_video_path) -> None:
    """Verify bundled short flags still set each paired setting."""
    _run_clean(["-dc", str(mock_video_path)])

    assert captured_clean["config"].downmix_stereo is True
    assert captured_clean["config"].keep_commentary is True


def test_positive_and_negative_flag_together_is_usage_error(capsys, mock_video_path) -> None:
    """Verify `--downmix` and `--no-downmix` cannot be combined."""
    code = _run_clean(["--downmix", "--no-downmix", str(mock_video_path)])

    capsys.readouterr()
    assert code == 2


def test_overwrite_from_config_applies(captured_clean, user_config, mock_video_path) -> None:
    """Verify `overwrite` in the config file takes effect with no flag."""
    user_config("overwrite = true")

    _run_clean([str(mock_video_path)])

    assert captured_clean["config"].overwrite is True


def test_run_flags_reach_run_options(captured_clean, mock_video_path) -> None:
    """Verify flag-only values arrive in `RunOptions`."""
    _run_clean(["-vv", "--h265", str(mock_video_path)])

    options = captured_clean["options"]
    assert options.verbosity == 2
    assert options.h265 is True
    assert options.dryrun is True


@pytest.mark.parametrize("subcommand", ["clean", "clip", "cache", "check", "search", "inspect"])
def test_help_works_with_invalid_config(capsys, user_config, subcommand) -> None:
    """Verify `--help` never loads the config."""
    user_config('radarr_url = "nope"')

    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=[subcommand, "--help"], deps=[configure_output])

    capsys.readouterr()
    assert exc_info.value.code == 0


def test_check_runs_with_invalid_config(capsys, user_config, mock_video_path) -> None:
    """Verify a command that reads no settings is not stopped by a bad config."""
    user_config('radarr_url = "nope"')

    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=["check", str(mock_video_path)], deps=[configure_output])

    assert "radarr_url" not in capsys.readouterr().err


def test_invalid_config_stops_clean(capsys, user_config, mock_video_path) -> None:
    """Verify a bad config stops a command that reads settings, naming the setting."""
    user_config('radarr_url = "nope"')

    code = _run_clean([str(mock_video_path)])

    assert code == 1
    assert "radarr_url" in capsys.readouterr().err


def test_default_config_created_private(capsys) -> None:
    """Verify the user config file, which can hold API keys, is readable only by its owner."""
    create_default_config()

    capsys.readouterr()
    assert stat.S_IMODE(constants.USER_CONFIG_PATH.stat().st_mode) == 0o600


def test_trace_output_hides_api_keys(capsys, user_config, captured_clean, mock_video_path) -> None:
    """Verify the `-vv` settings dump never prints an API key."""
    user_config('tmdb_api_key = "k3y-value"')

    _run_clean(["-vv", str(mock_video_path)])

    captured = capsys.readouterr()
    assert "tmdb_api_key" in captured.out + captured.err
    assert "k3y-value" not in captured.out + captured.err
