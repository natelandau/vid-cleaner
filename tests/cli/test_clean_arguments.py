"""Test how the clean subcommand parses and validates its arguments."""

import cappa
import pytest
from cappa.base import invoke

from tests.helpers import run_vidcleaner
from vid_cleaner.cli.clean_video import H265_AND_VP9_ERROR
from vid_cleaner.cli.discovery_output import (
    FROM_WITH_FILES_ERROR,
    NO_FILES_OR_FROM_ERROR,
    NOT_AN_EXISTING_DIRECTORY_ERROR,
    REQUIRES_FROM_ERROR,
)
from vid_cleaner.utils.cli import (
    LIMIT_BELOW_ONE_ERROR,
    OUT_WITH_FROM_ERROR,
    OUT_WITH_MULTIPLE_FILES_ERROR,
)
from vid_cleaner.vidcleaner import VidCleaner, configure_output


def test_fail_on_flag_conflict(capsys, video_path) -> None:
    """Verify clean command fails when incompatible flags are used."""
    # Given: Conflicting codec conversion flags
    args = ["clean", "-vv", "--h265", "--vp9", str(video_path)]

    # When: Running clean command with conflicting flags
    exit_code = run_vidcleaner(args)

    # Then: Error message is displayed
    output = capsys.readouterr().err

    assert exit_code == 1
    assert H265_AND_VP9_ERROR in output


def test_clean_out_with_multiple_files_errors(tmp_path, capsys) -> None:
    """Verify clean command rejects --out when given more than one input file."""
    # Given: an explicit --out alongside two input files
    first = tmp_path / "first_video.mkv"
    first.touch()
    second = tmp_path / "second_video.mkv"
    second.touch()
    args = ["clean", "-vv", "--out", str(tmp_path / "out.mkv"), str(first), str(second)]

    # When: running clean with --out and multiple files
    exit_code = run_vidcleaner(args)

    # Then: the command exits with an error explaining the conflict
    output = capsys.readouterr().err
    assert exit_code == 1
    assert OUT_WITH_MULTIPLE_FILES_ERROR in output


def test_clean_out_accepts_a_video_named_with_its_macos_sidecar(
    tmp_path, stub_output_write, mock_ffmpeg, mock_video_probe, mock_original_language
) -> None:
    """Verify --out works when a shell glob also matched the video's `._` metadata file."""
    # Given: one video and its AppleDouble sidecar, as `*.mkv` would expand
    video = tmp_path / "movie.mkv"
    video.touch()
    sidecar = tmp_path / "._movie.mkv"
    sidecar.write_bytes(b"\x00\x05\x16\x07")
    mock_video_probe("reference.json")
    out = tmp_path / "out.mkv"
    args = ["clean", "--h265", "--verify=off", "--out", str(out), str(sidecar), str(video)]

    # When: running clean with --out
    exit_code = run_vidcleaner(args)

    # Then: the one video is written to the --out path
    assert exit_code == 0
    assert stub_output_write.call_args.kwargs["dst"] == out


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
    assert FROM_WITH_FILES_ERROR in capsys.readouterr().err


def test_clean_rejects_from_with_out(capsys, tmp_path):
    """Verify --out is refused with --from, since one path cannot name a multi-file selection."""
    # Given: A discovery root and an explicit output path
    args = ["clean", "--from", str(tmp_path), "--out", str(tmp_path / "out.mkv")]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors
    assert exit_code == 1
    assert OUT_WITH_FROM_ERROR in capsys.readouterr().err


@pytest.mark.parametrize(
    "discovery_arg",
    [
        ["--filters", "h264"],
        ["--limit", "2"],
        ["--depth", "1"],
        ["--reverse"],
        ["--sort", "size"],
        ["--yes"],
    ],
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
    assert REQUIRES_FROM_ERROR in capsys.readouterr().err


def test_clean_rejects_no_files_and_no_from(capsys):
    """Verify clean with neither a file nor --from is a usage error."""
    # Given: No files and no discovery root
    args = ["clean"]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors
    assert exit_code == 1
    assert NO_FILES_OR_FROM_ERROR in capsys.readouterr().err


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
def test_clean_short_flag_matches_long_flag(captured_clean, video_path, short, long, attribute):
    """Verify each short flag sets the same setting or run option as its long form."""

    def setting_after(flags: list[str]) -> object:
        # A run that fails before planning must not leave the previous run's values behind
        captured_clean.clear()
        assert run_vidcleaner(["clean", "-n", *flags, str(video_path)]) == 0
        config, options = captured_clean["config"], captured_clean["options"]
        return getattr(config, attribute, getattr(options, attribute, None))

    # When: Running clean with the short form, the long form, and neither
    from_short = setting_after([short])
    from_long = setting_after([long])
    unset = setting_after([])

    # Then: Both forms reach the command with the same non-default value
    assert from_short == from_long
    assert from_short != unset


def test_clean_langs_short_flag_takes_a_value(captured_clean, video_path):
    """Verify -l accepts a language list rather than being treated as a boolean."""
    # When: Passing languages via the short flag
    run_vidcleaner(["clean", "-n", "-l", "es,fr", str(video_path)])

    # Then: The languages reach the settings
    assert captured_clean["config"].langs_to_keep == ("es", "fr")


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
    assert LIMIT_BELOW_ONE_ERROR in str(exc_info.value.message)


@pytest.mark.parametrize(
    ("name", "create"),
    [
        pytest.param("does-not-exist", False, id="missing"),
        pytest.param("movie.mkv", True, id="a file"),
    ],
)
def test_clean_rejects_from_that_is_not_a_directory(capsys, tmp_path, name, create):
    """Verify a --from that is missing or a file fails instead of reporting a successful no-op."""
    # Given: A --from path that is not an existing directory
    from_path = tmp_path / name
    if create:
        from_path.touch()
    args = ["clean", "--from", str(from_path)]

    # When: Running clean
    exit_code = run_vidcleaner(args)

    # Then: The command errors rather than exiting 0
    assert exit_code == 1
    assert f"`--from` {NOT_AN_EXISTING_DIRECTORY_ERROR}" in capsys.readouterr().err
