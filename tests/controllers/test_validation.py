# type: ignore
"""Test the video file validator behind the `check` subcommand."""

from pathlib import Path

import pytest
from box import Box

from vid_cleaner.constants import CodecTypes
from vid_cleaner.controllers.validation import check_video_file
from vid_cleaner.exceptions import VideoProbeError


def test_valid_file_passes(mock_video_path, mock_ffprobe_box, mocker):
    """Verify a probeable file carrying a video stream is reported valid."""
    # Given: A file that probes cleanly and has a real video stream
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    # When: Checking it
    result = check_video_file(mock_video_path)

    # Then: It passes with no reason
    assert result.ok is True
    assert result.reason is None
    assert result.path == mock_video_path


def test_missing_file_fails(tmp_path):
    """Verify a path that does not exist fails before any probe is attempted."""
    # Given: A path with nothing at it
    missing = tmp_path / "missing.mkv"

    # When: Checking it
    result = check_video_file(missing)

    # Then: It fails with an existence reason
    assert result.ok is False
    assert result.reason == "file does not exist"


def test_directory_fails(tmp_path):
    """Verify a directory carrying a video extension is not treated as a file."""
    # Given: A directory named like a video file
    directory = tmp_path / "season.mkv"
    directory.mkdir()

    # When: Checking it
    result = check_video_file(directory)

    # Then: It fails rather than being probed
    assert result.ok is False
    assert result.reason == "file does not exist"


def test_non_video_extension_fails(tmp_path):
    """Verify a real file without a video container extension fails."""
    # Given: A text file
    notes = tmp_path / "notes.txt"
    notes.write_text("not a video")

    # When: Checking it
    result = check_video_file(notes)

    # Then: It fails on its extension
    assert result.ok is False
    assert result.reason == "not a video file"


def test_unprobeable_file_reports_ffprobe_reason(mock_video_path, mocker):
    """Verify ffprobe's own diagnostic is surfaced as the failure reason."""
    # Given: A file ffprobe refuses to read
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(path=mock_video_path, reason="moov atom not found"),
    )

    # When: Checking it
    result = check_video_file(mock_video_path)

    # Then: The reason is ffprobe's, not a generic message
    assert result.ok is False
    assert result.reason == "moov atom not found"


def test_unlaunchable_ffprobe_fails_the_file_not_the_run(mock_video_path, mocker):
    """Verify an ffprobe that cannot run is reported as a verdict rather than raised."""
    # Given: ffprobe is missing from PATH
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=FileNotFoundError(2, "No such file or directory", "ffprobe"),
    )

    # When: Checking the file
    result = check_video_file(mock_video_path)

    # Then: The batch gets a verdict naming the missing binary
    assert result.ok is False
    assert "ffprobe" in result.reason


def test_audio_only_file_fails(mock_video_path, mock_ffprobe_box, mocker):
    """Verify a file that probes cleanly but carries no video stream fails."""
    # Given: A file whose only stream is audio
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("audio_only.json"),
    )

    # When: Checking it
    result = check_video_file(mock_video_path)

    # Then: It fails for lacking video
    assert result.ok is False
    assert result.reason == "no video stream"


def test_cover_art_does_not_count_as_video(mock_video_path, mock_ffprobe_box, mocker):
    """Verify an embedded poster cannot pass a file that has no real video stream.

    Containers routinely carry cover art as an mjpeg or png "video" stream, so a raw
    codec_type test would wave an audio-only file through.
    """
    # Given: An audio-only file with an mjpeg cover art stream
    probe_box = mock_ffprobe_box("audio_only.json")
    probe_box.streams.append(
        Box(
            {
                "index": 1,
                "codec_name": "mjpeg",
                "codec_type": CodecTypes.VIDEO,
                "tags": {},
            },
            default_box=True,
            default_box_create_on_get=False,
        )
    )
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=probe_box,
    )

    # When: Checking it
    result = check_video_file(mock_video_path)

    # Then: The cover art does not rescue it
    assert result.ok is False
    assert result.reason == "no video stream"


def test_deep_check_runs_full_decode(mock_video_path, mock_ffprobe_box, mocker):
    """Verify `--deep` decodes the file and passes when ffmpeg reports nothing."""
    # Given: A file that probes cleanly and decodes without error
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    decode = mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value=None,
    )

    # When: Checking it deeply
    result = check_video_file(mock_video_path, deep=True)

    # Then: The decode ran and the file passed
    assert result.ok is True
    decode.assert_called_once_with(mock_video_path)


def test_deep_check_reports_decode_error(mock_video_path, mock_ffprobe_box, mocker):
    """Verify a file with a clean header but a corrupt body fails under `--deep`."""
    # Given: A file that probes cleanly but fails to decode
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value="Error while decoding stream #0:0",
    )

    # When: Checking it deeply
    result = check_video_file(mock_video_path, deep=True)

    # Then: ffmpeg's decode error is the reason
    assert result.ok is False
    assert result.reason == "Error while decoding stream #0:0"


def test_shallow_check_never_decodes(mock_video_path, mock_ffprobe_box, mocker):
    """Verify the default tier stays metadata-only so a library scan stays fast."""
    # Given: A file that probes cleanly
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    decode = mocker.patch("vid_cleaner.controllers.validation.decode_check")

    # When: Checking it without `--deep`
    check_video_file(mock_video_path)

    # Then: No decode was attempted
    decode.assert_not_called()


@pytest.mark.parametrize("suffix", [".mkv", ".MKV", ".mp4", ".avi"])
def test_extension_matching_is_case_insensitive(tmp_path, suffix, mocker, mock_ffprobe_box):
    """Verify an uppercase container extension is accepted."""
    # Given: A file whose extension differs only in case
    path = Path(tmp_path / f"video{suffix}")
    path.touch()
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    # When: Checking it
    result = check_video_file(path)

    # Then: The extension does not disqualify it
    assert result.ok is True
