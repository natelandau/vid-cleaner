"""API utilities."""

import requests
from nclutils import console, pp

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

    if pp.is_trace:
        args = "&".join([f"{k}={v}" for k, v in params.items()])
        pp.trace(f"TMDB: Querying {url}?{args}")

    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        pp.error(str(e))
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        pp.error(
            f"Error querying The Movie Database API: {response.status_code} {response.reason}",
        )
        return {}

    pp.trace("TMDB: Response received")
    if pp.is_trace:
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
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        pp.error(str(e))
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        pp.error(f"Error querying Radarr: {response.status_code} {response.reason}")
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
        response = requests.get(url, params=params, timeout=15)
    except Exception as e:  # noqa: BLE001
        pp.error(str(e))
        return {}

    if response.status_code != 200:  # noqa: PLR2004
        pp.error(f"Error querying Sonarr: {response.status_code} {response.reason}")
        return {}

    pp.trace("SONARR: Response received")
    return response.json()
