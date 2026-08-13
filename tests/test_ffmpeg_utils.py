"""Test ffprobe output normalization."""

from pathlib import Path

import pytest
from nclutils.sh import ShellCommandNotFoundError

from vid_cleaner.constants import CodecTypes
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import get_probe_as_box
from vid_cleaner.utils.ffmpeg_utils import decode_check


@pytest.mark.parametrize(
    "stream",
    [
        pytest.param({"index": 0, "codec_name": "bin_data"}, id="missing_codec_type"),
        pytest.param({"index": 0, "codec_type": "nonsense"}, id="unknown_codec_type"),
        pytest.param({"index": 0, "codec_type": 12}, id="non_string_codec_type"),
    ],
)
def test_get_probe_as_box_rejects_undescribable_streams(mocker, stream: dict) -> None:
    """Verify a stream this model cannot describe raises VideoProbeError rather than crashing."""
    # Given: ffprobe succeeds but reports a stream with no usable codec type
    mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_ffprobe",
        return_value={"format": {"filename": "x.mkv"}, "streams": [stream]},
    )

    # When: Normalizing the probe output
    # Then: The file is reported as unreadable
    with pytest.raises(VideoProbeError):
        get_probe_as_box(Path("x.mkv"))


def test_get_probe_as_box_defaults_missing_codec_name(mocker) -> None:
    """Verify a stream without a codec name normalizes to an empty string."""
    # Given: ffprobe reports a video stream whose codec it could not identify
    mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_ffprobe",
        return_value={
            "format": {"filename": "x.mkv"},
            "streams": [{"index": 0, "codec_type": "video"}],
        },
    )

    # When: Normalizing the probe output
    probe_box = get_probe_as_box(Path("x.mkv"))

    # Then: Downstream `.lower()` calls have a string to work with
    assert probe_box.streams[0].codec_type == CodecTypes.VIDEO
    assert probe_box.streams[0].codec_name == ""


def test_decode_check_passes_on_a_clean_decode(mocker, tmp_path) -> None:
    """Verify a file ffmpeg decodes without complaint reports no error."""
    # Given: ffmpeg exits zero having written nothing to stderr
    run_command = mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_command",
        return_value=mocker.Mock(stderr_lines=[], ok=True, returncode=0),
    )

    # When: Decoding the file
    result = decode_check(tmp_path / "movie.mkv")

    # Then: Nothing is reported and the decode went to the null muxer
    assert result is None
    argv = run_command.call_args.args[0]
    assert argv[:3] == ["ffmpeg", "-v", "error"]
    assert argv[-3:] == ["-f", "null", "-"]
    # Every video and audio stream is decoded, not just the one ffmpeg would pick
    assert argv[argv.index("-i") + 2 :][:4] == ["-map", "0:v?", "-map", "0:a?"]


def test_decode_check_reports_the_first_error_line(mocker, tmp_path) -> None:
    """Verify ffmpeg's own diagnostic is returned rather than a generic message."""
    # Given: ffmpeg writes decode errors to stderr
    mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_command",
        return_value=mocker.Mock(
            stderr_lines=[
                "  Error while decoding stream #0:0: Invalid data found  ",
                "[matroska @ 0x14f] Read error",
            ],
            ok=False,
            returncode=1,
        ),
    )

    # When: Decoding the file
    result = decode_check(tmp_path / "movie.mkv")

    # Then: The first line is returned, stripped
    assert result == "Error while decoding stream #0:0: Invalid data found"


def test_decode_check_reports_a_silent_non_zero_exit(mocker, tmp_path) -> None:
    """Verify a non-zero exit with an empty stderr is still reported as a failure."""
    # Given: ffmpeg fails without explaining itself
    mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_command",
        return_value=mocker.Mock(stderr_lines=["", "   "], ok=False, returncode=137),
    )

    # When: Decoding the file
    result = decode_check(tmp_path / "movie.mkv")

    # Then: The exit code carries the report
    assert result == "ffmpeg exited with code 137"


def test_decode_check_reports_a_missing_ffmpeg(mocker, tmp_path) -> None:
    """Verify a missing or unrunnable ffmpeg fails the file instead of the run."""
    # Given: ffmpeg is not on PATH
    mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.run_command",
        side_effect=ShellCommandNotFoundError(argv=["ffmpeg"]),
    )

    # When: Decoding the file
    result = decode_check(tmp_path / "movie.mkv")

    # Then: The reason is reported rather than raised
    assert result is not None
    assert "ffmpeg" in result
