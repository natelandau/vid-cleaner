"""Shared fixtures for tests."""

import json
from pathlib import Path

import pytest
from nclutils.pytest_fixtures import clean_stdout, debug  # noqa: F401
from rich.console import Console

from vid_cleaner.utils import get_probe_as_box

console = Console()


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
def mock_ffprobe_box(mocker):
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

        mocker.patch(
            "vid_cleaner.utils.ffmpeg_utils.run_ffprobe",
            return_value=json.loads("".join(line for line in cleaned_content)),
        )

        return get_probe_as_box(fixture)

    return _inner


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
