"""Shared fixtures for tests."""

import copy
import json
from pathlib import Path

import pytest
from box import Box
from rich.console import Console

from vid_cleaner import settings
from vid_cleaner.utils import get_probe_as_box

console = Console()


@pytest.fixture(autouse=True)
def _reset_verbosity():
    """Reset settings.verbosity to its default before and after every test.

    `settings` is a process-wide singleton, so a test that persists a CLI
    verbosity flag (e.g. via `config_subcommand`) would otherwise leak that
    value into unrelated tests run later in the same session.
    """
    settings.update({"verbosity": 0})
    yield
    settings.update({"verbosity": 0})


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


@pytest.fixture
def video_library(tmp_path, mocker, mock_ffprobe_box):
    """Build a directory of probeable video files with per-file size and bitrate.

    Names may include a nested path, e.g. "aaa/mike.mkv", to exercise recursive
    searches; the parent directory is created automatically.

    Returns:
        Callable[[list[tuple[str, int, int]]], Path]: Call with `(filename, size_bytes,
            bitrate_bps)` triples to create the files and patch ffprobe to report the
            matching bitrate for each one.
    """

    def _inner(files: list[tuple[str, int, int]]) -> Path:
        reference = mock_ffprobe_box("reference.json")
        directory = tmp_path / "library"
        directory.mkdir(exist_ok=True)

        boxes = {}
        for name, size, bitrate in files:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\0" * size)
            file_box = copy.deepcopy(reference)
            file_box.bit_rate = str(bitrate) if bitrate else None
            boxes[name] = file_box

        mocker.patch(
            "vid_cleaner.models.video_file.get_probe_as_box",
            side_effect=lambda path: boxes[path.relative_to(directory).as_posix()],
        )
        return directory

    return _inner


@pytest.fixture
def mock_probe_tags(mocker):
    """Patch VideoFile.probe_box to expose only the given container format tags.

    Lets language-discovery tests control `format.tags` without a full ffprobe fixture.

    Usage:
        def test_something(mock_probe_tags):
            mock_probe_tags({"TMDB": "movie/1399"})

    Returns:
        Callable[[dict[str, str] | None], None]: Call with a tags mapping (or nothing
            for no tags) to patch `get_probe_as_box` accordingly.
    """

    def _inner(tags: dict[str, str] | None = None) -> None:
        mocker.patch(
            "vid_cleaner.models.video_file.get_probe_as_box",
            return_value=Box({"format": {"tags": tags or {}}}, default_box=True),
        )

    return _inner
