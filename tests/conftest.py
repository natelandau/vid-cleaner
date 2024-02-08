# type: ignore
"""Shared fixtures."""

from pathlib import Path

import pytest
from confz import DataSource, FileSource
from loguru import logger

from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.utils import console

logger.remove()  # Remove default logger

FIXTURE_CONFIG = Path(__file__).resolve().parent.parent / "fixtures/configs/default_config.toml"


@pytest.fixture(autouse=True)
def _change_test_dir(monkeypatch, tmp_path):
    """All tests should run in a temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def mock_config():  # noqa: PT004
    """Override configuration file with mock configuration for use in tests. To override a default use the `mock_specific_config` fixture.

    Returns:
        VidCleanerConfig: The mock configuration.
    """
    with VidCleanerConfig.change_config_sources([FileSource(FIXTURE_CONFIG)]):
        yield


@pytest.fixture()
def mock_specific_config():
    """Mock specific configuration data for use in tests."""

    def _inner(
        log_to_file: bool | None = None,
        log_file: str | None = None,
        keep_languages: list[str] | None = None,
        radarr_api_key: str | None = None,
        radarr_url: str | None = None,
        sonarr_api_key: str | None = None,
        sonarr_url: str | None = None,
        tmdb_api_key: str | None = None,
    ):
        override_data = {}
        if log_to_file:
            override_data["log_to_file"] = log_to_file
        if log_file:
            override_data["log_file"] = log_file
        if keep_languages:
            override_data["keep_languages"] = keep_languages
        if radarr_api_key:
            override_data["radarr_api_key"] = radarr_api_key
        if radarr_url:
            override_data["radarr_url"] = radarr_url
        if sonarr_api_key:
            override_data["sonarr_api_key"] = sonarr_api_key
        if sonarr_url:
            override_data["sonarr_url"] = sonarr_url
        if tmdb_api_key:
            override_data["tmdb_api_key"] = tmdb_api_key

        return [FileSource(FIXTURE_CONFIG), DataSource(data=override_data)]

    return _inner


@pytest.fixture()
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
