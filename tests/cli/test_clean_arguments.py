"""Test how the clean subcommand parses and validates its arguments."""

from pathlib import Path

import cappa
import pytest
from cappa.base import invoke, parse

from tests.helpers import run_vidcleaner
from vid_cleaner.cli.discovery_output import NOT_AN_EXISTING_DIRECTORY_ERROR
from vid_cleaner.vidcleaner import CleanCommand, VidCleaner, configure_output


def test_fail_on_flag_conflict(capsys, video_path) -> None:
    """Verify clean command fails when incompatible flags are used."""
    # Given: Conflicting codec conversion flags
    args = ["clean", "-vv", "--h265", "--vp9", str(video_path)]

    # When: Running clean command with conflicting flags
    exit_code = run_vidcleaner(args)

    # Then: Error message is displayed
    output = capsys.readouterr().err

    assert exit_code == 1
    assert "Cannot convert to both H265 and VP9" in output


def test_clean_out_with_multiple_files_errors(tmp_path, capsys) -> None:
    """Verify clean command rejects --out when given more than one input file."""
    # Given: an explicit --out alongside two input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()
    args = ["clean", "-vv", "--out", str(tmp_path / "out.mkv"), str(first), str(second)]

    # When: running clean with --out and multiple files
    exit_code = run_vidcleaner(args)

    # Then: the command exits with an error explaining the conflict
    output = capsys.readouterr().err
    assert exit_code == 1
    assert "`--out` cannot be used with multiple input files" in output


def test_clean_rejects_from_with_positional_files(capsys, tmp_path):
    """Verify --from and explicit files are refused together as conflicting sources."""
    # Given: Both a discovery root and an explicit file
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--from", str(tmp_path), str(video)]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors explaining the conflict
    assert exit_code == 1
    assert "`--from` cannot be combined with explicit file paths" in capsys.readouterr().err


def test_clean_rejects_from_with_out(capsys, tmp_path):
    """Verify --out is refused with --from, since one path cannot name a multi-file selection."""
    # Given: A discovery root and an explicit output path
    args = ["clean", "--from", str(tmp_path), "--out", str(tmp_path / "out.mkv")]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors
    assert exit_code == 1
    assert "`--out` cannot be used with `--from`" in capsys.readouterr().err


@pytest.mark.parametrize(
    "discovery_arg",
    [["--filters", "h264"], ["--limit", "2"], ["--depth", "1"], ["--reverse"], ["--sort", "size"]],
)
def test_clean_rejects_discovery_flags_without_from(capsys, tmp_path, discovery_arg):
    """Verify a discovery flag without --from errors rather than silently doing nothing."""
    # Given: A discovery flag on an explicit-file run
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", str(video), *discovery_arg]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors naming --from
    assert exit_code == 1
    assert "require `--from`" in capsys.readouterr().err


def test_clean_rejects_no_files_and_no_from(capsys):
    """Verify clean with neither a file nor --from is a usage error."""
    # Given: No files and no discovery root
    args = ["clean"]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors
    assert exit_code == 1
    assert "Provide file path(s) or `--from`" in capsys.readouterr().err


def test_clean_rejects_yes_without_from(capsys, tmp_path):
    """Verify --yes without --from errors rather than being accepted as a no-op."""
    # Given: --yes on an explicit-file run, where no prompt would ever be shown
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--yes", str(video)]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors naming --from
    assert exit_code == 1
    assert "require `--from`" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("short", "long", "attribute"),
    [
        ("-H", "--h265", "h265"),
        ("-V", "--vp9", "vp9"),
        ("-p", "--1080p", "video_1080"),
        ("-d", "--downmix", "downmix_stereo"),
        ("-D", "--drop-original", "drop_original_audio"),
        ("-s", "--keep-subs", "keep_all_subtitles"),
        ("-S", "--keep-local-subs", "keep_local_subtitles"),
        ("-x", "--drop-local-subs", "drop_local_subs"),
        ("-c", "--keep-commentary", "keep_commentary"),
        ("-f", "--force", "force"),
        ("-w", "--overwrite", "overwrite"),
    ],
)
def test_clean_short_flag_matches_long_flag(tmp_path, short, long, attribute):
    """Verify each short flag toggles the same option as its long form."""
    # Given: A clean invocation naming one file
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the short form and the long form of the same option
    from_short = parse(VidCleaner, argv=["clean", short, str(video)]).command
    from_long = parse(VidCleaner, argv=["clean", long, str(video)]).command
    unset = parse(VidCleaner, argv=["clean", str(video)]).command

    # Then: Both set the same field, and to something other than the default
    assert getattr(from_short, attribute) == getattr(from_long, attribute)
    assert getattr(from_short, attribute) != getattr(unset, attribute)


def test_clean_short_flags_bundle(tmp_path):
    """Verify short flags combine into one token so common conversions stay terse."""
    # Given: A clean invocation bundling several boolean short flags
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the bundled form
    command = parse(VidCleaner, argv=["clean", "-Hdsc", str(video)]).command
    assert isinstance(command, CleanCommand)

    # Then: Every bundled flag is set
    assert command.h265
    assert command.downmix_stereo
    assert command.keep_all_subtitles
    assert command.keep_commentary


def test_clean_langs_short_flag_takes_a_value(tmp_path):
    """Verify -l accepts a language list rather than being treated as a boolean."""
    # Given: A clean invocation passing languages via the short flag
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the short form
    command = parse(VidCleaner, argv=["clean", "-l", "es,fr", str(video)]).command
    assert isinstance(command, CleanCommand)

    # Then: The value lands in langs_to_keep
    assert command.langs_to_keep == "es,fr"


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_clean_rejects_limit_below_one(tmp_path, limit):
    """Verify a limit below one is refused instead of silently selecting the wrong files."""
    # Given: A discovery run asking for a zero or negative limit
    args = ["clean", "--from", str(tmp_path), "--limit", limit]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The command fails with a message naming the acceptable range
    assert exc_info.value.code != 0
    assert "must be 1 or greater" in str(exc_info.value.message)


def test_clean_rejects_from_that_does_not_exist(capsys, tmp_path):
    """Verify a missing --from directory fails instead of reporting a successful no-op."""
    # Given: A --from path that does not exist
    args = ["clean", "--from", str(tmp_path / "does-not-exist")]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors rather than exiting 0
    assert exit_code == 1
    assert f"`--from` {NOT_AN_EXISTING_DIRECTORY_ERROR}" in capsys.readouterr().err


def test_clean_rejects_from_that_is_a_file(capsys, tmp_path):
    """Verify a --from pointing at a file fails, since clean otherwise takes file paths."""
    # Given: A --from path naming a file rather than a directory
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--from", str(video)]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors rather than exiting 0
    assert exit_code == 1
    assert f"`--from` {NOT_AN_EXISTING_DIRECTORY_ERROR}" in capsys.readouterr().err
