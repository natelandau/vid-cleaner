"""API utilities."""

import httpx
from nllog import error, trace
from rich.json import JSON

from vid_cleaner import settings


def query_tmdb(search: str) -> dict:  # pragma: no cover
    """Query The Movie Database API for a movie title.

    Args:
        search (str): IMDB id (tt____) to search for

    Returns:
        dict: The Movie Database API response
    """
    tmdb_api_key = settings.TMDB_API_KEY

    if not tmdb_api_key:
        return {}

    url = f"https://api.themoviedb.org/3/find/{search}"

    params = {
        "api_key": tmdb_api_key,
        "language": "en-US",
        "external_source": "imdb_id",
    }

    args = "&".join([f"{k}={v}" for k, v in params.items()])
    trace(f"TMDB: Query {url}?{args}")

    try:
        response = httpx.get(url, params=params, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        error(str(e))
        return {}

    trace("TMDB: Response received", details=[JSON(response.text)])

    return response.json()


def query_radarr(search: str) -> dict:  # pragma: no cover
    """Query Radarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Radarr API response
    """
    radarr_url = settings.RADARR_URL
    radarr_api_key = settings.RADARR_API_KEY

    if not radarr_api_key or not radarr_url:
        return {}

    url = f"{radarr_url}/api/v3/parse"
    params = {
        "apikey": radarr_api_key,
        "title": search,
    }

    try:
        response = httpx.get(url, params=params, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        error(str(e))
        return {}

    trace("RADARR: Response received", details=[JSON(response.text)])

    return response.json()


def query_sonarr(search: str) -> dict:  # pragma: no cover
    """Query Sonarr API for a movie title.

    Args:
        search (str): Movie title to search for
        api_key (str): Radarr API key

    Returns:
        dict: Sonarr API response
    """
    sonarr_url = settings.SONARR_URL
    sonarr_api_key = settings.SONARR_API_KEY

    if not sonarr_api_key or not sonarr_url:
        return {}

    url = f"{sonarr_url}/api/v3/parse"
    params = {
        "apikey": sonarr_api_key,
        "title": search,
    }

    try:
        response = httpx.get(url, params=params, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        error(str(e))
        return {}

    trace("SONARR: Response received", details=[JSON(response.text)])

    return response.json()
