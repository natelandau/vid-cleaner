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
    # Match whole comment lines only, so a value such as a URL tag keeps its `//`
    return json.loads("\n".join(line for line in lines if not line.lstrip().startswith("//")))


def read_json_document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """Parse the JSON report a run wrote to stdout.

    Consume the captured output, so a test that also needs stderr reads both from one
    `capsys.readouterr()` call instead.

    Args:
        capsys: The pytest capture fixture holding the run's output.

    Returns:
        dict[str, Any]: The parsed report document.
    """
    return json.loads(capsys.readouterr().out)


def set_file_size(path: Path, size: int) -> None:
    """Give a file a size on disk without writing its bytes, for code that reads file sizes.

    Args:
        path: The file to create or resize.
        size: The size in bytes.
    """
    with path.open("r+b" if path.exists() else "wb") as file:
        file.truncate(size)


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


def leftover_cache_files(cache_dir: Path) -> list[Path]:
    """List the files still under a cache directory, to assert a run cleaned up its temp files.

    Args:
        cache_dir: The cache directory to search recursively.

    Returns:
        list[Path]: Every file under `cache_dir`, directories excluded.
    """
    return [path for path in cache_dir.rglob("*") if path.is_file()]
