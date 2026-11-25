"""Helpers shared across test modules."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import cappa
import pytest
from cappa.base import invoke

from vid_cleaner.vidcleaner import VidCleaner, configure_output

FFPROBE_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "ffprobe"


def load_ffprobe_json(filename: str) -> dict[str, Any]:
    """Load a recorded ffprobe response from `tests/fixtures/ffprobe`.

    The fixture files carry `//` comment lines describing the media they stand for, which
    JSON does not allow, so those lines are dropped before parsing.

    Args:
        filename: The fixture file name, such as `reference.json`.

    Returns:
        dict[str, Any]: The parsed ffprobe output.
    """
    lines = (FFPROBE_FIXTURES_DIR / filename).read_text().splitlines()
    return json.loads("\n".join(line for line in lines if "//" not in line))


def run_vidcleaner(
    argv: list[str], *, deps: Sequence[Callable[..., object]] = (configure_output,)
) -> str | int | None:
    """Run the real `vidcleaner` command line and return its exit code.

    Use this instead of calling `main()` so each test exercises argument parsing,
    dependency injection, and the command handler together. Every command ends by
    raising `cappa.Exit`, so a run that returns normally fails the test.

    Args:
        argv: The arguments after the program name, such as `["clean", "-n", path]`.
        deps: Functions cappa runs before the command. Defaults to output setup only, which
            skips the ffmpeg and ffprobe preflight that `main()` adds.

    Returns:
        str | int | None: The exit code the command finished with.
    """
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=argv, deps=list(deps))
    return exc_info.value.code


def ffmpeg_command(mock_ffmpeg: MagicMock) -> str:
    """Return the one ffmpeg command a run issued, as a single string.

    Args:
        mock_ffmpeg: The `mock_ffmpeg` fixture after the run.

    Returns:
        str: The command's arguments joined with spaces, for substring assertions.
    """
    mock_ffmpeg.assert_called_once()
    return " ".join(mock_ffmpeg.call_args.args[0])
