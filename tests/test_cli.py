# type: ignore
"""Test video-transcode CLI."""

import re

from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from video_transcode.cli import app

runner = CliRunner()


def test_version():
    """Test printing version and then exiting."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert re.match(r"video_transcode: v\d+\.\d+\.\d+", strip_ansi(result.output))
