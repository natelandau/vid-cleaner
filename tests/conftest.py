# type: ignore
"""Shared fixtures."""

import json
from pathlib import Path

import pytest
from confz import DataSource, FileSource
from loguru import logger

from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.utils import console

logger.remove()  # Remove default logger

FIXTURE_CONFIG = Path(__file__).resolve().parent.parent / "src/vid_cleaner/default_config.toml"


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

    """
    mock_ffmpeg_progress = mocker.patch(
        "vid_cleaner.models.video_file.FfmpegProgress", autospec=True
    )
    mock_instance = mock_ffmpeg_progress.return_value
    mock_instance.run_command_with_progress.return_value = iter([0, 25, 50, 75, 100])
    return mock_ffmpeg_progress


@pytest.fixture
def mock_video(tmp_path):
    """Fixture to return a VideoFile instance with a specified path."""
    # GIVEN a VideoFile instance with a specified path
    test_path = Path(tmp_path / "test_video.mp4")
    test_path.touch()  # Create a dummy file
    return VideoFile(test_path)


@pytest.fixture(autouse=True)
def _change_test_dir(monkeypatch, tmp_path):
    """All tests should run in a temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_config(tmp_path):
    """Mock specific configuration data for use in tests by accepting arbitrary keyword arguments.

    The function dynamically collects provided keyword arguments, filters out any that are None,
    and prepares data sources with the overridden configuration for file processing.

    Usage:
        def test_something(mock_config):
            # Override the configuration with specific values
            with VidCleanerConfig.change_config_sources(config_data(some_key="some_value")):
                    # Test the functionality
                    result = do_something()
                    assert result
    """

    def _inner(**kwargs):
        """Collects provided keyword arguments, omitting any that are None, and prepares data sources with the overridden configuration.

        Args:
            **kwargs: Arbitrary keyword arguments representing configuration settings.

        Returns:
            list: A list containing a FileSource initialized with the fixture configuration and a DataSource with the overridden data.
        """
        # Filter out None values from kwargs
        override_data = {key: value for key, value in kwargs.items() if value is not None}

        # If a 'config.toml' file exists in the test directory, use it as the configuration source
        if Path(tmp_path / "config.toml").exists():
            config_file_source = str(tmp_path / "config.toml")
        else:
            # Check for 'config_file' in kwargs and use it if present, else default to FIXTURE_CONFIG
            config_file_source = kwargs.get("config_file", FIXTURE_CONFIG)

        # Return a list of data sources with the overridden configuration
        return [FileSource(config_file_source), DataSource(data=override_data)]

    return _inner


@pytest.fixture
def debug():
    """Print debug information to the console. This is used to debug tests while writing them."""

    def _debug_inner(label: str, value: str | Path, breakpoint: bool = False):
        """Print debug information to the console. This is used to debug tests while writing them.

        Args:
            label (str): The label to print above the debug information.
            value (str | Path): The value to print. When this is a path, prints all files in the path.
            breakpoint (bool, optional): Whether to break after printing. Defaults to False.

        Returns:
            bool: Whether to break after printing.
        """
        console.rule(label)
        if not isinstance(value, Path) or not value.is_dir():
            console.print(value)
        else:
            for p in value.rglob("*"):
                console.print(p)

        console.rule()

        if breakpoint:
            return pytest.fail("Breakpoint")

        return True

    return _debug_inner


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
