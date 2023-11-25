"""Helper functions for video-transcode."""

import shutil
from pathlib import Path

import ffmpeg as python_ffmpeg
import requests
import typer
from loguru import logger

from video_transcode.config import Config
from video_transcode.constants import APP_DIR


def existing_file_path(path: str) -> Path:
    """Check if path exists and is a file."""
    resolved_path = Path(path).expanduser().resolve()

    if not resolved_path.exists():
        msg = f"File {path!s} does not exist"
        raise typer.BadParameter(msg)

    if not resolved_path.is_file():
        msg = f"{path!s} is not a file"
        raise typer.BadParameter(msg)

    return resolved_path


def ffprobe(path: Path) -> dict:  # Pragma: no cover
    """Probe video file and return a dict.

    Args:
        path (Path): Path to video file
    """
    try:
        probe = python_ffmpeg.probe(path)
    except python_ffmpeg.Error as e:
        logger.error(e.stderr)
        raise typer.Exit(1) from e

    return probe


def query_tmdb(search: str) -> dict:  # Pragma: no cover
    """Query The Movie Database API for a movie title.

    Args:
        search (str): IMDB id (tt____) to search for
        api_key (str): The Movie Database API key

    Returns:
        dict: The Movie Database API response
    """
    config = Config(config_path=APP_DIR / "config.toml")
    if not config.get("tmdb_api_key", pass_none=True):
        return {}

    url = f"https://api.themoviedb.org/3/find/{search}"

    params = {
        "api_key": config.get("tmdb_api_key"),
        "language": "en-US",
        "external_source": "imdb_id",
    }

    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.error(e)
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        logger.error(
            f"Error querying The Movie Database API: {response.status_code} {response.reason}"
        )
        return {}

    logger.trace("TMDB: Response received")
    return response.json()


def query_radarr(search: str) -> dict:  # Pragma: no cover
    """Query Radarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Radarr API response
    """
    config = Config(config_path=APP_DIR / "config.toml")

    if not config.get("radarr_api_key", pass_none=True):
        return {}
    if not config.get("radarr_url", pass_none=True):
        return {}

    radarr_url = config.get("radarr_url")
    url = f"{radarr_url}/api/v3/parse"
    params = {
        "apikey": config.get("radarr_api_key"),
        "title": search,
    }

    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.error(e)
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        logger.error(f"Error querying Radarr: {response.status_code} {response.reason}")
        return {}

    return response.json()


def query_sonarr(search: str) -> dict:  # Pragma: no cover
    """Query Sonarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Sonarr API response
    """
    config = Config(config_path=APP_DIR / "config.toml")

    if not config.get("sonarr_api_key", pass_none=True):
        return {}
    if not config.get("sonarr_url", pass_none=True):
        return {}

    sonarr_url = config.get("sonarr_url")
    url = f"{sonarr_url}/api/v3/parse"
    params = {
        "apikey": config.get("sonarr_api_key"),
        "title": search,
    }

    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.error(e)
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        logger.error(f"Error querying Sonarr: {response.status_code} {response.reason}")
        return {}

    logger.trace("SONARR: Response received")
    return response.json()


def tmp_to_output(
    tmp_file: Path,
    stem: str,
    overwrite: bool = False,
    new_file: Path | None = None,
) -> Path:
    """Copy a temporary file to an output file.

    Args:
        tmp_file (Path): Path to input file
        stem (str): Stem of output file
        new_file (Path, optional): Path to output file. Defaults to None.
        overwrite (bool, optional): Overwrite output file if it exists. Defaults to False.

    Returns:
        Path: Path to output file
    """
    # When a path is given, use that
    if new_file:
        parent = new_file.parent.expanduser().resolve()
        stem = new_file.stem
    else:
        parent = Path.cwd()

    # Ensure parent directory exists
    parent.mkdir(parents=True, exist_ok=True)

    new = parent / f"{stem}{tmp_file.suffix}"

    if not overwrite:
        i = 1
        while new.exists():
            new = parent / f"{stem}_{i}{tmp_file.suffix}"
            i += 1

    shutil.copy(tmp_file, new)
    logger.trace(f"File copied to {new}")
    return new