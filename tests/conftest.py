"""Shared fixtures for tests."""

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.console import Console

console = Console()


@pytest.fixture
def clean_stdout(capsys: pytest.CaptureFixture[str]) -> Callable[[], str]:
    r"""Return a function that cleans ANSI escape sequences from captured stdout.

    This fixture is useful for testing CLI output where ANSI color codes and other escape sequences need to be stripped to verify the actual text content. The returned callable captures stdout using pytest's capsys fixture and removes all ANSI escape sequences, making it easier to write assertions against the cleaned output.

    Args:
        capsys (pytest.CaptureFixture[str]): Pytest fixture that captures stdout/stderr streams

    Returns:
        Callable[[], str]: A function that when called returns the current stdout with all ANSI escape sequences removed

    Example:
        def test_cli_output(clean_stdout):
            print("\033[31mRed Text\033[0m")  # Colored output
            assert clean_stdout() == "Red Text"  # Test against clean text
    """
    ansi_chars = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

    def _get_clean_stdout() -> str:
        return ansi_chars.sub("", capsys.readouterr().out)

    return _get_clean_stdout


@pytest.fixture
def debug() -> Callable[[str | Path, str, bool, int], bool]:
    """Create a debug printing function for test development.

    Return a function that prints formatted debug output with clear visual separation and optional breakpoints. Useful for inspecting variables, file contents, or directory structures during test development.

    Returns:
        Callable[[str | Path, str, bool, int], bool]: Debug printing function with parameters:
            - value: Data to debug print (string or Path)
            - label: Optional header text
            - breakpoint: Whether to pause execution after printing
            - width: Maximum output width in characters
    """

    def _debug_inner(
        value: str | Path,
        label: str = "",
        *,
        breakpoint: bool = False,
        width: int = 80,
    ) -> bool:
        """Print formatted debug information during test development.

        Format and display debug output with labeled headers and clear visual separation. Supports printing file contents, directory structures, and variable values with optional execution breakpoints.

        Args:
            value (str | Path): Value to debug print. For Path objects, prints directory tree
            label (str): Optional header text for context
            breakpoint (bool, optional): Pause execution after printing. Defaults to False
            width (int, optional): Maximum output width. Defaults to 80

        Returns:
            bool: True unless breakpoint is True, then raises pytest.fail()
        """
        console.rule(label or "")

        # If a directory is passed, print the contents
        if isinstance(value, Path) and value.is_dir():
            for p in value.rglob("*"):
                console.print(p, width=width)
        else:
            console.print(value, width=width)

        console.rule()

        if breakpoint:
            return pytest.fail("Breakpoint")

        return True

    return _debug_inner


@pytest.fixture
def mock_video_path(tmp_path):
    """Fixture to return a VideoFile instance with a specified path.

    Returns:
        VideoFile: A VideoFile instance with a specified path.
    """
    # GIVEN a VideoFile instance with a specified path
    test_path = Path(tmp_path / "test_video.mp4")
    test_path.touch()  # Create a dummy file
    return test_path


@pytest.fixture
def mock_ffprobe():
    """Return mocked JSON response from ffprobe."""

    def _inner(filename: str):
        fixture = Path(__file__).resolve().parent / "fixtures/ffprobe" / filename

        cleaned_content = []  # Remove comments from JSON
        with fixture.open() as f:
            for line in f.readlines():
                # Remove comments
                if "//" in line:
                    continue
                cleaned_content.append(line)

        return json.loads("".join(line for line in cleaned_content))

    return _inner


@pytest.fixture
def mock_ffmpeg(mocker):
    """Fixture to mock the FfmpegProgress class to effectively mock the ffmpeg command and its progress output.

    Usage:
        def test_something(mock_ffmpeg):
            # Mock the FfmpegProgress class
            mock_ffmpeg_progress = mock_ffmpeg()

            # Test the functionality
            do_something()
            mock_ffmpeg.assert_called_once() # Confirm that the ffmpeg command was called once
            args, _ = mock_ffmpeg.call_args # Grab the ffmpeg command arguments
            command = " ".join(args[0]) # Join the arguments into a single string
            assert command == "ffmpeg -i input.mp4 output.mp4" # Check the command

    Returns:
        Mock: A mock object for the FfmpegProgress class.
    """
    mock_ffmpeg_progress = mocker.patch(
        "vid_cleaner.models.video_file.FfmpegProgress",
        autospec=True,
    )
    mock_instance = mock_ffmpeg_progress.return_value
    mock_instance.run_command_with_progress.return_value = iter([0, 25, 50, 75, 100])
    return mock_ffmpeg_progress
