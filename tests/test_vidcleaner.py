"""Test the vidcleaner command line interface."""

import stat
from pathlib import Path

import cappa
import pytest
from cappa.base import invoke

from tests.helpers import run_vidcleaner
from vid_cleaner import constants
from vid_cleaner.config import Config, RunOptions
from vid_cleaner.models.conversion_plan import ConversionPlan
from vid_cleaner.utils import create_default_config
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


def test_cache_runs_without_ffmpeg(
    capsys,
    mocker,
) -> None:
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
    exit_code = run_vidcleaner(args)

    # Then: The usage-error exit code and message name the flag and the bad value
    _, error = capsys.readouterr()
    assert exit_code == 2
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

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", _record)
    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_changes_file", return_value=False)
    return captured


def _run_clean(argv: list[str]) -> str | int | None:
    return run_vidcleaner(["clean", "-n", *argv])


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
    captured_clean, user_config, video_path, file_value, flags, expected
) -> None:
    """Verify a track flag means what it says whatever the config file holds."""
    user_config(f"keep_commentary = {file_value}")

    code = _run_clean([*flags, str(video_path)])

    assert code == 0
    assert captured_clean["config"].keep_commentary is expected


def test_bundled_short_flags_set_each_setting(captured_clean, video_path) -> None:
    """Verify bundled short flags still set each paired setting."""
    _run_clean(["-dc", str(video_path)])

    assert captured_clean["config"].downmix_stereo is True
    assert captured_clean["config"].keep_commentary is True


def test_positive_and_negative_flag_together_is_usage_error(capsys, video_path) -> None:
    """Verify `--downmix` and `--no-downmix` cannot be combined."""
    code = _run_clean(["--downmix", "--no-downmix", str(video_path)])

    capsys.readouterr()
    assert code == 2


def test_overwrite_from_config_applies(captured_clean, user_config, video_path) -> None:
    """Verify `overwrite` in the config file takes effect with no flag."""
    user_config("overwrite = true")

    _run_clean([str(video_path)])

    assert captured_clean["config"].overwrite is True


def test_run_flags_reach_run_options(captured_clean, video_path) -> None:
    """Verify flag-only values arrive in `RunOptions`."""
    _run_clean(["-vv", "--h265", str(video_path)])

    options = captured_clean["options"]
    assert options.verbosity == 2
    assert options.h265 is True
    assert options.dryrun is True


@pytest.mark.parametrize("subcommand", ["clean", "clip", "cache", "check", "search", "inspect"])
def test_help_works_with_invalid_config(capsys, user_config, subcommand) -> None:
    """Verify `--help` never loads the config."""
    user_config('radarr_url = "nope"')

    exit_code = run_vidcleaner([subcommand, "--help"])

    capsys.readouterr()
    assert exit_code == 0


def test_check_runs_with_invalid_config(capsys, user_config, video_path) -> None:
    """Verify a command that reads no settings is not stopped by a bad config."""
    user_config('radarr_url = "nope"')

    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=["check", str(video_path)], deps=[configure_output])

    assert "radarr_url" not in capsys.readouterr().err


def test_invalid_config_stops_clean(capsys, user_config, video_path) -> None:
    """Verify a bad config stops a command that reads settings, naming the setting."""
    user_config('radarr_url = "nope"')

    code = _run_clean([str(video_path)])

    assert code == 1
    assert "radarr_url" in capsys.readouterr().err


def test_default_config_created_private(capsys) -> None:
    """Verify the user config file, which can hold API keys, is readable only by its owner."""
    create_default_config()

    capsys.readouterr()
    assert stat.S_IMODE(constants.USER_CONFIG_PATH.stat().st_mode) == 0o600


def test_trace_output_hides_api_keys(capsys, user_config, captured_clean, video_path) -> None:
    """Verify the `-vv` settings dump never prints an API key."""
    user_config('tmdb_api_key = "k3y-value"')

    _run_clean(["-vv", str(video_path)])

    captured = capsys.readouterr()
    assert "tmdb_api_key" in captured.out + captured.err
    assert "k3y-value" not in captured.out + captured.err
