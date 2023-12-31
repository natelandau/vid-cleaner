# type: ignore
"""Test configuration model."""

import filecmp
import shutil
from pathlib import Path

import pytest
import typer

from vid_cleaner.config.config import PATH_CONFIG_DEFAULT, Config


def test_init_config_1():
    """Test initializing a configuration file.

    GIVEN a request to initialize a configuration file
    WHEN no path is provided
    THEN raise an exception
    """
    with pytest.raises(typer.Exit):
        Config()


def test_init_config_2(tmp_path):
    """Test initializing a configuration file.

    GIVEN a request to initialize a configuration file
    WHEN a path to a non-existent file is provided
    THEN create the default configuration file and exit
    """
    config_path = Path(tmp_path / "config.toml")
    with pytest.raises(typer.Exit):
        Config(config_path=config_path)
    assert config_path.exists()
    assert filecmp.cmp(config_path, PATH_CONFIG_DEFAULT) is True


def test_init_config_3(tmp_path):
    """Test initializing a configuration file.

    GIVEN a request to initialize a configuration file
    WHEN a path to the default configuration file is provided
    THEN load the configuration file
    """
    path_to_config = Path(tmp_path / "config.toml")
    shutil.copy(PATH_CONFIG_DEFAULT, path_to_config)
    config = Config(config_path=path_to_config, context={"dry_run": False})
    assert config.config_path == path_to_config
    assert config.config == {
        "dry_run": False,
        "keep_languages": ["en"],
        "log_file": "log.txt",
        "log_to_file": True,
        "radarr_api_key": "",
        "radarr_url": "",
        "sonarr_api_key": "",
        "sonarr_url": "",
        "tmdb_api_key": "",
    }
    assert config.context == {"dry_run": False}


def test_init_config_4(tmp_path):
    """Test initializing a configuration file.

    GIVEN a request to initialize a configuration file
    WHEN values are provided in the context
    THEN load the configuration file
    """
    path_to_config = Path(tmp_path / "config.toml")
    shutil.copy(PATH_CONFIG_DEFAULT, path_to_config)
    config = Config(config_path=path_to_config, context={"dry_run": True, "force": True})
    assert config.config_path == path_to_config
    assert config.config == {
        "dry_run": True,
        "keep_languages": ["en"],
        "log_file": "log.txt",
        "log_to_file": True,
        "force": True,
        "radarr_api_key": "",
        "radarr_url": "",
        "sonarr_api_key": "",
        "sonarr_url": "",
        "tmdb_api_key": "",
    }
    assert config.context == {"dry_run": True, "force": True}
