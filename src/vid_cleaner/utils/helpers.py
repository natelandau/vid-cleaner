"""Helper functions for vid-cleaner."""

import io
import shutil
from collections.abc import Callable
from pathlib import Path

import ffmpeg as python_ffmpeg
import requests
import typer
from loguru import logger
from rich.progress import Progress

from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.constants import BUFFER_SIZE
from vid_cleaner.utils import errors

from .console import console


def existing_file_path(path: str) -> Path:
    """Check if the given path exists and is a file.

    Args:
        path (str): The path to check.

    Returns:
        Path: The resolved path if it exists and is a file.

    Raises:
        typer.BadParameter: If the path does not exist or is not a file.
    """
    resolved_path = Path(path).expanduser().resolve()

    if not resolved_path.exists():
        msg = f"File {path!s} does not exist"
        raise typer.BadParameter(msg)

    if not resolved_path.is_file():
        msg = f"{path!s} is not a file"
        raise typer.BadParameter(msg)

    return resolved_path


def ffprobe(path: Path) -> dict:  # pragma: no cover
    """Probe video file and return a dict.

    Args:
        path (Path): Path to video file

    Returns:
        dict: A dictionary containing information about the video file.
    """
    try:
        probe = python_ffmpeg.probe(path)
    except python_ffmpeg.Error as e:
        logger.error(e.stderr)
        raise typer.Exit(1) from e

    return probe


def query_tmdb(search: str, verbosity: int) -> dict:  # pragma: no cover
    """Query The Movie Database API for a movie title.

    Args:
        search (str): IMDB id (tt____) to search for
        verbosity (int): Verbosity level

    Returns:
        dict: The Movie Database API response
    """
    tmdb_api_key = VidCleanerConfig().tmdb_api_key

    if not tmdb_api_key:
        return {}

    url = f"https://api.themoviedb.org/3/find/{search}"

    params = {
        "api_key": tmdb_api_key,
        "language": "en-US",
        "external_source": "imdb_id",
    }

    if verbosity > 1:
        args = "&".join([f"{k}={v}" for k, v in params.items()])
        logger.trace(f"TMDB: Querying {url}?{args}")

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
    if verbosity > 1:
        console.log(response.json())
    return response.json()


def query_radarr(search: str) -> dict:  # pragma: no cover
    """Query Radarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Radarr API response
    """
    radarr_url = VidCleanerConfig().radarr_url
    radarr_api_key = VidCleanerConfig().radarr_api_key

    if not radarr_api_key or not radarr_url:
        return {}

    url = f"{radarr_url}/api/v3/parse"
    params = {
        "apikey": radarr_api_key,
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


def query_sonarr(search: str) -> dict:  # pragma: no cover
    """Query Sonarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Sonarr API response
    """
    sonarr_url = VidCleanerConfig().sonarr_url
    sonarr_api_key = VidCleanerConfig().sonarr_api_key

    if not sonarr_api_key or not sonarr_url:
        return {}

    url = f"{sonarr_url}/api/v3/parse"
    params = {
        "apikey": sonarr_api_key,
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


def _copyfileobj(
    src_bytes: io.BufferedReader,
    dest_bytes: io.BufferedWriter,
    callback: Callable,
    length: int,
) -> None:
    """Copy bytes from a source file to a destination file, with callback support.

    This function reads bytes from a source file and writes them to a destination file in chunks,
    calling a specified callback function after each chunk is written. It continues this process
    until all bytes are copied or the end of the source file is reached.

    Args:
        src_bytes: The source file from which to read bytes. Must support the buffer protocol.
        dest_bytes: The destination file to which bytes are written. Must support the buffer protocol.
        callback: A callable that is invoked after each chunk of bytes is copied. The callable
                  should accept a single argument, which is the total number of bytes copied so far.
        length: The size of each chunk of bytes to be read and written at a time.
    """
    copied = 0
    while True:
        buf = src_bytes.read(length)
        if not buf:
            break
        dest_bytes.write(buf)
        copied += len(buf)
        if callback is not None:
            callback(copied)


def copy_with_callback(
    src: Path,
    dest: Path,
    callback: Callable | None = None,
    buffer_size: int = BUFFER_SIZE,
) -> Path:
    """Copy a file from a source to a destination, with optional progress callback.

    This function copies a file from a specified source path to a destination path. During the
    copy operation, it can optionally call a provided callback function after each chunk of data
    is copied, allowing for progress tracking or other notifications. The size of the chunks copied
    at each step can be customized.

    Args:
        src: The path of the source file to copy.
        dest: The path of the destination file or directory. If a directory is provided, the source
              file will be copied into this directory with the same filename.
        callback: An optional callable that is invoked after each chunk of data is copied. The callback
                  receives one argument: the number of bytes copied so far. If not provided, no callback
                  is called.
        buffer_size: The size of each chunk of data to copy, in bytes. This determines how often the
                     callback function is called, if provided.

    Returns:
        The path to the copied destination file.

    Raises:
        FileNotFoundError: If the source file does not exist.
        SameFileError: If the source and destination paths refer to the same file.
        ValueError: If the `callback` is provided but is not callable.

    Note:
        This function does not copy file metadata such as extended attributes or resource forks.
    """
    if not src.is_file():
        msg = f"src file `{src}` doesn't exist"
        raise FileNotFoundError(msg)

    dest = dest / src.name if dest.is_dir() else dest

    if dest.exists() and src.samefile(dest):
        msg = f"source file `{src}` and destination file `{dest}` are the same file."
        raise errors.SameFileError(msg)

    if callback is not None and not callable(callback):
        msg = f"callback must be callable, not {type(callback)}"  # type: ignore [unreachable]
        raise ValueError(msg)

    with src.open("rb") as src_bytes, dest.open("wb") as dest_bytes:
        _copyfileobj(src_bytes, dest_bytes, callback=callback, length=buffer_size)

    shutil.copymode(str(src), str(dest))

    return dest


def tmp_to_output(
    tmp_file: Path,
    stem: str,
    overwrite: bool = False,
    new_file: Path | None = None,
) -> Path:
    """Copy a temporary file to an output location with optional renaming and overwrite control.

    This function copies a temporary file to a specified output location. If the output file path is not provided, the function uses the current working directory and the provided stem for the file name. If the target file exists and overwrite is False, the function will append a number to the stem to create a unique filename.

    Args:
        tmp_file: The path to the temporary input file to be copied.
        stem: The base name (stem) to use for the output file if `new_file` is not provided. If `new_file` is provided, `stem` is ignored.
        overwrite: A flag to indicate whether to overwrite the output file if it already exists. If False and the file exists, a number is appended to the file's stem to avoid overwriting.
        new_file: An optional path to the output file. If provided, this path is used as the target for the copy operation, and `stem` is ignored.

    Returns:
        The path to the output file where the temporary file has been copied.

    Note:
        This function uses a progress bar to indicate the copy progress and handles file naming conflicts by appending a number to the file stem if `overwrite` is False.
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

    tmp_file_size = tmp_file.stat().st_size

    with Progress(transient=True) as progress:
        task = progress.add_task("Copy file…", total=tmp_file_size)
        copy_with_callback(
            tmp_file,
            new,
            callback=lambda total_copied: progress.update(task, completed=total_copied),
        )

    logger.trace(f"File copied to {new}")
    return new
