# type: ignore
"""Test vid-cleaner CLI."""

import re

from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from vid_cleaner.vid_cleaner import app

runner = CliRunner()


def test_version():
    """Verify version flag prints correct version format and exits cleanly."""
    # GIVEN a CLI app

    # WHEN invoking with --version flag
    result = runner.invoke(app, ["--version"])

    # THEN verify successful exit
    assert result.exit_code == 0
    # AND verify version output matches semantic versioning format
    assert re.match(r"vid_cleaner: v\d+\.\d+\.\d+", strip_ansi(result.output))
