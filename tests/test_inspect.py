"""Test the vidcleaner inspect subcommand."""

import re
from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


def test_inspect_table(tmp_path, clean_stdout, debug, mock_video_path, mock_ffprobe_box, mocker):
    """Verify inspect command displays video information in table format."""
    # Given: Mock video file and ffprobe data
    args = ["inspect", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    # When: Running inspect command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Output contains expected table data
    output = clean_stdout()
    assert exc_info.value.code == 0
    assert re.search(r"0 │ video +│ h264", output)
    assert re.search(r"9 │ video +│ mjpeg", output)
    assert re.search(r"eng +│ 8 +│ 7.1", output)
    assert re.search(r"1920 +│ 1080 +│ Test", output)


def test_inspect_json(tmp_path, clean_stdout, debug, mock_video_path, mock_ffprobe, mocker):
    """Verify inspect command displays video information in JSON format."""
    # Given: Mock video file and ffprobe data
    args = ["inspect", "--json", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})
    mocker.patch(
        "vid_cleaner.models.video_file.run_ffprobe",
        return_value=mock_ffprobe("reference.json"),
    )

    # When: Running inspect command with JSON flag
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Output contains expected JSON data
    output = clean_stdout()
    assert exc_info.value.code == 0
    assert "'bit_rate': '26192239'," in output
    assert "'channel_layout': '7.1'," in output
    assert "'codec_name': 'hdmv_pgs_subtitle'," in output
