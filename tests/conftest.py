"""Shared fixtures for tests."""

import copy
import os
from dataclasses import replace
from pathlib import Path

import pytest
from box import Box
from iso639 import Lang
from nclutils import pp
from rich.console import Console

from tests.helpers import FFPROBE_FIXTURES_DIR, load_ffprobe_json, set_file_size
from vid_cleaner import constants
from vid_cleaner.config import Config, RunOptions, load_config
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.models.conversion_plan import ConversionPlan
from vid_cleaner.utils import OutputWrite, get_probe_as_box

from vid_cleaner.models.video_file import VideoFile  # isort: skip


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Keep the developer's own config files and `VIDCLEANER_*` variables out of every test.

    The loader reads the user and dev config paths from `constants` at call time, so
    pointing them at missing files under `tmp_path` leaves only the package default.
    """
    for variable in [name for name in os.environ if name.startswith("VIDCLEANER_")]:
        monkeypatch.delenv(variable)
    monkeypatch.setattr(constants, "USER_CONFIG_PATH", tmp_path / "user-config.toml")
    monkeypatch.setattr(constants, "DEV_CONFIG_PATH", tmp_path / "dev-config.toml")
    monkeypatch.setenv("VIDCLEANER_CACHE_DIR", str(tmp_path / "cache"))
    # CLI tests mock ffmpeg and never write a real output, so the default `quick` check would
    # fail them; verification tests turn it on with `--verify`, which beats this variable
    monkeypatch.setenv("VIDCLEANER_VERIFY", "off")


@pytest.fixture(autouse=True)
def _reset_output():
    """Start every test with stdout output at normal verbosity.

    `pp` keeps its console process-wide, so a test that runs without `configure_output`
    would otherwise print through whatever console the previous test left behind.
    """
    pp.configure(verbosity=0, console=Console(theme=pp.THEME))


@pytest.fixture
def user_config(tmp_path):
    """Write TOML to the user config file the loader reads in tests.

    Returns:
        Callable[[str], Path]: Writes the text and returns the file path.
    """

    def _write(text: str) -> Path:
        path = tmp_path / "user-config.toml"
        path.write_text(text)
        return path

    return _write


@pytest.fixture
def make_config(tmp_path):
    """Build a `Config` from the package defaults, with test overrides.

    Returns:
        Callable[..., Config]: Accepts `Config` field names as keyword overrides.
    """

    def _make(**overrides) -> Config:
        base = load_config(config_paths=[constants.DEFAULT_CONFIG_PATH], environ={}).config
        return replace(base, cache_dir=tmp_path / "cache", **overrides)

    return _make


@pytest.fixture
def video_path(tmp_path) -> Path:
    """Create an empty video file to pass where the code needs a real path on disk.

    Returns:
        Path: The empty `test_video.mp4` under `tmp_path`.
    """
    path = tmp_path / "test_video.mp4"
    path.touch()
    return path


@pytest.fixture
def mock_ffprobe_box(mocker):
    """Build the probe box for a recorded ffprobe response.

    Also patches `run_ffprobe` to return that response, so code that probes through
    `get_probe_as_box` sees the same data.

    Returns:
        Callable[[str], Box]: Call with a fixture file name to get its probe box.
    """

    def _inner(filename: str) -> Box:
        mocker.patch(
            "vid_cleaner.utils.ffmpeg_utils.run_ffprobe",
            return_value=load_ffprobe_json(filename),
        )
        return get_probe_as_box(FFPROBE_FIXTURES_DIR / filename)

    return _inner


@pytest.fixture
def mock_video_probe(mocker, mock_ffprobe_box):
    """Make every `VideoFile` probe return a recorded ffprobe response.

    Returns:
        Callable[[str], Box]: Call with a fixture file name; returns the box the probe
            now reports, so a test can adjust it.
    """

    def _inner(filename: str) -> Box:
        box = mock_ffprobe_box(filename)
        mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", return_value=box)
        return box

    return _inner


@pytest.fixture
def mock_unreadable_probe(mocker, mock_ffprobe_box):
    """Make the named files fail to probe and every other file probe as `reference.json`.

    Returns:
        Callable[..., Box]: Call with the file names ffprobe rejects; returns the box the
            readable files report.
    """

    def _inner(*unreadable_names: str) -> Box:
        readable_box = mock_ffprobe_box("reference.json")

        def _probe(path: Path) -> Box:
            if path.name in unreadable_names:
                raise VideoProbeError(path=path, reason="Invalid data found when processing input")
            return readable_box

        mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=_probe)
        return readable_box

    return _inner


@pytest.fixture
def mock_original_language(mocker):
    """Report English as each video's original language without querying TMDB, Radarr, or Sonarr.

    Returns:
        MagicMock: The patched lookup. Set `return_value` to report another language.
    """
    return mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))


@pytest.fixture
def stub_output_write(mocker):
    """Skip copying a processed file to its destination and report the save as done.

    Patches the copy shared by `clean` and `clip`. Use `fake_transcode` instead when the test
    needs the real write.

    Returns:
        MagicMock: The patched copy.
    """

    def _report_save(src: Path, dst: Path, *, overwrite: bool) -> OutputWrite:
        return OutputWrite(path=dst, backup_path=None)

    return mocker.patch("vid_cleaner.cli.ffmpeg_step.copy_to_output", side_effect=_report_save)


@pytest.fixture
def make_video(video_path, mock_video_probe, mock_original_language):
    """Build a `VideoFile` from a recorded ffprobe response without any network lookup.

    Returns:
        Callable[..., VideoFile]: Call with a fixture file name and, optionally, the
            original language to report (default `en`).
    """

    def _inner(fixture_name: str, language: str = "en") -> VideoFile:
        # Matching the probed path keeps `VideoFile` from re-probing on every access
        mock_video_probe(fixture_name).path_to_file = video_path.resolve()
        mock_original_language.return_value = Lang(language)
        return VideoFile(video_path)

    return _inner


@pytest.fixture
def mock_ffmpeg(mocker):
    """Replace ffmpeg with a stub that reports progress and writes nothing.

    Read the one command a run issued with `ffmpeg_command(mock_ffmpeg)` from
    `tests/helpers.py`. Use `fake_transcode` instead when the test needs a real output file.

    Returns:
        MagicMock: The patched `FfmpegProgress` class. Each call's first argument is the
            ffmpeg command as a list of strings.
    """
    mock_ffmpeg_progress = mocker.patch(
        "vid_cleaner.utils.ffmpeg_utils.FfmpegProgress",
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
            set_file_size(path, size)
            file_box = copy.deepcopy(reference)
            file_box.bit_rate = str(bitrate) if bitrate else None
            # Real probe boxes carry the path they were built from, and `VideoFile`
            # compares it to decide whether its cached probe is still current.
            file_box.path_to_file = path
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


@pytest.fixture
def interactive_console(mocker):
    """Force `Console.is_terminal` so the prompt path can be exercised under pytest.

    Patch the property on the class rather than replacing `pp.console`, which would hand
    `pp.error` a mock and silently break stderr capture in these same tests.

    Returns:
        Callable[..., None]: Call with `is_terminal=True` or `is_terminal=False` to set
            interactivity.
    """

    def _inner(*, is_terminal: bool) -> None:
        mocker.patch(
            "rich.console.Console.is_terminal",
            new_callable=mocker.PropertyMock,
            return_value=is_terminal,
        )

    return _inner


@pytest.fixture
def fake_transcode(mocker) -> None:
    """Replace the ffmpeg pass with one that writes a real temporary file.

    Lets a test exercise the genuine `copy_to_output` write, and so the output path and
    the fate of the original, rather than stubbing the write out.
    """

    def run_ffmpeg(command, *, on_progress):
        command.output_path.write_bytes(b"transcoded")

    mocker.patch("vid_cleaner.cli.ffmpeg_step.run_ffmpeg", run_ffmpeg)


@pytest.fixture
def captured_clean(mocker):
    """Record the settings `clean` hands to each file instead of running ffmpeg.

    Returns:
        dict[str, object]: Holds the `config` and `options` of the last cleaned file.
    """
    captured: dict[str, object] = {}

    def _record(self, config: Config, options: RunOptions) -> ConversionPlan:
        captured["config"] = config
        captured["options"] = options
        return ConversionPlan()

    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_clean", _record)
    mocker.patch("vid_cleaner.models.video_file.VideoFile.plan_changes_file", return_value=False)
    return captured
