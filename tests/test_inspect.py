"""Test the vidcleaner inspect subcommand."""

import re
from pathlib import Path

import cappa
import pytest

from vid_cleaner.utils import settings
from vid_cleaner.vidcleaner import VidCleaner


def test_inspect_table(tmp_path, clean_stdout, debug, mock_video_path, mock_ffprobe, mocker):
    """Verify printing a table of video information."""
    args = ["inspect", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "keep_languages": ["en"]})

    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    debug(output, "output")

    assert exc_info.value.code == 0
    assert re.search(r"0 │ video +│ h264", output)
    assert re.search(r"9 │ video +│ mjpeg", output)
    assert re.search(r"eng +│ 8 +│ 7.1", output)
    assert re.search(r"1920 +│ 1080 +│ Test", output)


def test_inspect_json(tmp_path, clean_stdout, debug, mock_video_path, mock_ffprobe, mocker):
    """Test printing json output of video information."""
    args = ["inspect", "--json", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "keep_languages": ["en"]})

    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )

    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args)

    output = clean_stdout()
    debug(output, "output")

    assert exc_info.value.code == 0
    assert "'bit_rate': '26192239'," in output
    assert "'channel_layout': '7.1'," in output
    assert "'codec_name': 'hdmv_pgs_subtitle'," in output
