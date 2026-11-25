"""Test the vidcleaner command line interface."""

import json
import os
import signal
from pathlib import Path

import pytest

from tests.helpers import run_vidcleaner
from vid_cleaner.vidcleaner import (
    STOP_SIGNALS,
    configure_output,
    handle_stop_signals,
    verify_required_binaries,
)


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


def test_missing_binaries_with_json_writes_an_error_report(capsys, mocker) -> None:
    """Verify --json reports the missing programs in the document and keeps stdout parseable."""
    # Given: Both programs are absent
    mocker.patch("vid_cleaner.utils.cli.which", return_value=None)

    # When: Running a JSON command
    exit_code = run_vidcleaner(
        ["inspect", "--json", "movie.mkv"], deps=[configure_output, verify_required_binaries]
    )

    # Then: stdout is one report naming them, and the human message went to stderr
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert exit_code == 1
    assert "ffmpeg, ffprobe" in doc["error"]
    assert doc["files"] == []
    assert "ffmpeg, ffprobe" in captured.err


def test_present_binaries_allow_the_run(mocker, tmp_path) -> None:
    """Verify the preflight is silent when both programs are on PATH."""
    # Given: Both programs are present
    mocker.patch("vid_cleaner.utils.cli.which", return_value=Path("/usr/bin/ffmpeg"))

    # When: Running the preflight for a command that needs them
    args = ["check", str(tmp_path / "movie.mkv")]
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
    assert "Not found on PATH" not in captured.err + captured.out


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


@pytest.mark.parametrize(
    "cache_dir_line",
    [
        pytest.param("", id="valid cache_dir"),
        pytest.param('cache_dir = "relative"', id="bad cache_dir"),
    ],
)
@pytest.mark.parametrize("subcommand", ["check", "search"])
def test_query_commands_run_with_invalid_config(
    capsys, monkeypatch, user_config, video_path, mock_video_probe, subcommand, cache_dir_line
) -> None:
    """Verify a command that reads no settings is not stopped by a bad config."""
    mock_video_probe("reference.json")
    # The test cache directory variable would hide the file's cache_dir
    monkeypatch.delenv("VIDCLEANER_CACHE_DIR")
    user_config(f'radarr_url = "nope"\n{cache_dir_line}')
    target = str(video_path) if subcommand == "check" else str(video_path.parent)

    exit_code = run_vidcleaner([subcommand, target])

    assert exit_code == 0
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


def test_trace_output_hides_api_keys(capsys, user_config, captured_clean, video_path) -> None:
    """Verify the `-vv` settings dump never prints an API key."""
    user_config('tmdb_api_key = "k3y-value"')

    _run_clean(["-vv", str(video_path)])

    captured = capsys.readouterr()
    assert "tmdb_api_key" in captured.out + captured.err
    assert "k3y-value" not in captured.out + captured.err


@pytest.fixture
def restore_signal_handlers():
    """Put back the signal handlers a test replaces, so later tests see pytest's own."""
    saved = {stop_signal: signal.getsignal(stop_signal) for stop_signal in STOP_SIGNALS}
    yield
    for stop_signal, handler in saved.items():
        signal.signal(stop_signal, handler)


@pytest.mark.parametrize("stop_signal", STOP_SIGNALS, ids=lambda s: s.name)
@pytest.mark.usefixtures("restore_signal_handlers")
def test_stop_signal_takes_the_ctrl_c_path(stop_signal: signal.Signals) -> None:
    """Verify kill, `docker stop`, and systemd stop the run the way Ctrl-C does.

    Ctrl-C's KeyboardInterrupt is what removes temporary files, so a signal that ended
    the process outright would leave full-size transcodes behind.
    """
    handle_stop_signals()

    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), stop_signal)


@pytest.mark.parametrize("stop_signal", STOP_SIGNALS, ids=lambda s: s.name)
@pytest.mark.usefixtures("restore_signal_handlers")
def test_ignored_stop_signal_stays_ignored(stop_signal: signal.Signals) -> None:
    """Verify a run started under `nohup` keeps running when the terminal closes."""
    signal.signal(stop_signal, signal.SIG_IGN)

    handle_stop_signals()

    assert signal.getsignal(stop_signal) is signal.SIG_IGN
