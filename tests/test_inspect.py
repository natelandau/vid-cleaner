# type: ignore
"""Test the inspect command."""

import re

from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.vid_cleaner import app

runner = CliRunner()


def test_inspect_table(mock_config, debug, mock_video, mock_ffprobe, mocker):
    """Test printing a table of video information."""
    # Setup mocks
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )

    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["inspect", str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    assert result.exit_code == 0
    assert re.search(r"0 │ video +│ h264", output)
    assert re.search(r"9 │ video +│ mjpeg", output)
    assert re.search(r"eng +│ 8 +│ 7.1", output)
    assert re.search(r"1920 +│ 1080 +│ Test", output)


def test_inspect_json(mock_config, debug, mock_video, mock_ffprobe, mocker):
    """Test printing json output of video information."""
    # Setup mocks
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )

    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["inspect", "--json", str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    assert result.exit_code == 0
    assert "'bit_rate': '26192239'," in output
    assert "'channel_layout': '7.1'," in output
    assert "'codec_name': 'hdmv_pgs_subtitle'," in output
