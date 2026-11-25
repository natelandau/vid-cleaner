"""Test the vidcleaner command line interface."""

import stat
from pathlib import Path

import pytest

from tests.helpers import run_vidcleaner
from vid_cleaner import constants
from vid_cleaner.utils import create_default_config
from vid_cleaner.vidcleaner import verify_required_binaries


@pytest.mark.parametrize("subcommand", ["inspect", "clip", "clean", "cache"])
def test_vidcleaner_cli_help(capsys, subcommand: str) -> None:
    """Verify help text displays for each subcommand."""
    # Given: Command line arguments requesting help
    # When: Invoking CLI with help flag
    run_vidcleaner([subcommand, "--help"])

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
    exit_code = run_vidcleaner(args, deps=[verify_required_binaries])

    captured = capsys.readouterr()

    # Then: The run stops and names every absent program
    assert exit_code == 1
    assert expected in captured.err


def test_present_binaries_allow_the_run(mocker) -> None:
    """Verify the preflight is silent when both programs are on PATH."""
    # Given: Both programs are present
    mocker.patch("vid_cleaner.utils.cli.which", return_value=Path("/usr/bin/ffmpeg"))

    # When: Running the preflight for a command that needs them
    args = ["check", "movie.mkv"]
    exit_code = run_vidcleaner(args, deps=[verify_required_binaries])

    # Then: The run reaches the command itself rather than stopping at the preflight
    assert exit_code == 1  # `movie.mkv` does not exist


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
    exit_code = run_vidcleaner(["cache"], deps=[verify_required_binaries])

    captured = capsys.readouterr()

    # Then: The preflight does not block it
    assert exit_code == 0
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


def _run_clean(argv: list[str]) -> str | int | None:
    return run_vidcleaner(["clean", "-n", *argv])


@pytest.mark.parametrize(
    ("file_value", "flags", "expected"),
    [
        ("true", ["--no-keep-commentary"], False),
        ("false", ["--keep-commentary"], True),
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
    """Verify bundled short flags set each setting and run option they stand for."""
    _run_clean(["-Hdsc", str(video_path)])

    assert captured_clean["options"].h265 is True
    assert captured_clean["config"].downmix_stereo is True
    assert captured_clean["config"].keep_all_subtitles is True
    assert captured_clean["config"].keep_commentary is True


def test_positive_and_negative_flag_together_is_usage_error(video_path) -> None:
    """Verify `--downmix` and `--no-downmix` cannot be combined."""
    code = _run_clean(["--downmix", "--no-downmix", str(video_path)])

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
def test_help_works_with_invalid_config(user_config, subcommand) -> None:
    """Verify `--help` never loads the config."""
    user_config('radarr_url = "nope"')

    exit_code = run_vidcleaner([subcommand, "--help"])

    assert exit_code == 0


def test_check_runs_with_invalid_config(capsys, user_config, video_path) -> None:
    """Verify a command that reads no settings is not stopped by a bad config."""
    user_config('radarr_url = "nope"')

    run_vidcleaner(["check", str(video_path)])

    assert "radarr_url" not in capsys.readouterr().err


def test_invalid_config_stops_clean(capsys, user_config, video_path) -> None:
    """Verify a bad config stops a command that reads settings, naming the setting."""
    user_config('radarr_url = "nope"')

    code = _run_clean([str(video_path)])

    assert code == 1
    assert "radarr_url" in capsys.readouterr().err


def test_config_warnings_are_shown_and_the_run_continues(
    capsys, captured_clean, user_config, video_path
) -> None:
    """Verify config warnings, such as an unknown setting, reach the user without stopping the run."""
    user_config("keep_comentary = true")

    code = _run_clean([str(video_path)])

    output, error = capsys.readouterr()
    assert code == 0
    assert "keep_comentary" in output + error
    assert "config" in captured_clean


def test_default_config_created_private() -> None:
    """Verify the user config file, which can hold API keys, is readable only by its owner."""
    create_default_config()

    assert stat.S_IMODE(constants.USER_CONFIG_PATH.stat().st_mode) == 0o600


def test_default_config_never_replaces_an_existing_file(capsys) -> None:
    """Verify an existing user config, with its settings and API keys, is left as written."""
    # Given: A user config the user has edited
    constants.USER_CONFIG_PATH.write_text('tmdb_api_key = "mine"\n')

    # When: Creating the default config, as every launch does
    create_default_config()

    # Then: The file is unchanged and no creation is announced
    assert constants.USER_CONFIG_PATH.read_text() == 'tmdb_api_key = "mine"\n'
    assert "created" not in capsys.readouterr().out


def test_trace_output_hides_api_keys(capsys, user_config, captured_clean, video_path) -> None:
    """Verify the `-vv` settings dump never prints an API key."""
    user_config('tmdb_api_key = "k3y-value"')

    _run_clean(["-vv", str(video_path)])

    captured = capsys.readouterr()
    assert "tmdb_api_key" in captured.out + captured.err
    assert "k3y-value" not in captured.out + captured.err
