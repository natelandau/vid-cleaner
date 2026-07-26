"""Test ffprobe output normalization."""

from pathlib import Path

import pytest

from vid_cleaner.constants import CodecTypes
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.utils import get_probe_as_box


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
