# type: ignore
"""Test vid-cleaner CLI."""

import re

from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from vid_cleaner.cli import app

runner = CliRunner()


def test_version():
    """Test printing version and then exiting."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert re.match(r"vid_cleaner: v\d+\.\d+\.\d+", strip_ansi(result.output))
