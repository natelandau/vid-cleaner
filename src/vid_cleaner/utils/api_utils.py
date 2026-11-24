"""API utilities."""

from typing import Any, Literal

import httpx
from nclutils import pp
from rich.json import JSON

from .media_ids import MediaId

_TIMEOUT_SECONDS = 15


def describe_http_error(service: str, error: httpx.HTTPError) -> str:
    """Describe a failed request without its URL, which can carry an API key.

    Args:
        service (str): The service name to show, such as `TMDB`.
        error (httpx.HTTPError): The error httpx raised.

    Returns:
        str: A one-line description naming the service and the status or error type.
    """
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        return f"{service} request failed: {response.status_code} {response.reason_phrase}"
    return f"{service} request failed: {type(error).__name__}"


def _get_json(
    service: str,
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    """Fetch JSON from a service, reporting a failure without the URL that can carry a key.

    Returns:
        dict[str, Any] | None: The response body, an empty dict after a reported failure, or
            None for a 404 when `missing_ok` is set.
    """
    pp.trace(f"{service}: Query {url}")
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if missing_ok and e.response.status_code == httpx.codes.NOT_FOUND:
            return None
        pp.error(describe_http_error(service=service, error=e))
        return {}
    except httpx.HTTPError as e:
        pp.error(describe_http_error(service=service, error=e))
        return {}

    # A proxy login page or a wrong base URL answers 200 with HTML, which must not abort the run
    try:
        body = response.json()
    except ValueError:
        pp.error(
            f"{service} request failed: expected a JSON response, got "
            f"{response.headers.get('content-type', 'no content type')}"
        )
        return {}
    pp.trace(f"{service}: Response received", details=[JSON.from_data(body)])
    return body


def query_tmdb(search: str, *, api_key: str) -> dict[str, Any]:
    """Query The Movie Database API for a movie title by its IMDb ID.

    Args:
        search (str): IMDb ID (tt____) to search for.
        api_key (str): The TMDB API key. An empty key skips the request.

    Returns:
        dict: The Movie Database API response, or an empty dict when unavailable.
    """
    if not api_key:
        return {}

    params = {"api_key": api_key, "language": "en-US", "external_source": "imdb_id"}
    return _get_json("TMDB", f"https://api.themoviedb.org/3/find/{search}", params=params) or {}


def query_tmdb_by_id(
    tmdb_id: str, media_type: Literal["movie", "tv"] | None = None, *, api_key: str
) -> dict[str, Any]:
    """Query The Movie Database API by its own ID rather than an external ID.

    Args:
        tmdb_id (str): The TMDB numeric ID to look up.
        media_type (Literal["movie", "tv"] | None): The record type. When None, try
            movie first and fall back to tv.
        api_key (str): The TMDB API key. An empty key skips the request.

    Returns:
        dict: The Movie Database API response, or an empty dict when not found.
    """
    if not api_key:
        return {}

    for candidate_type in [media_type] if media_type else ["movie", "tv"]:
        # A 404 only means this was the wrong record type, so try the next one
        result = _get_json(
            "TMDB",
            f"https://api.themoviedb.org/3/{candidate_type}/{tmdb_id}",
            params={"api_key": api_key, "language": "en-US"},
            missing_ok=True,
        )
        if result is not None:
            return result

    return {}


def tmdb_original_language(media_id: MediaId, *, api_key: str) -> str | None:
    """Look up the original language TMDB records for a movie or series.

    Args:
        media_id (MediaId): An IMDb ID, which TMDB resolves through its find endpoint, or a
            TMDB ID.
        api_key (str): The TMDB API key. An empty key skips the request.

    Returns:
        str | None: The ISO 639 language code, or None when TMDB has no record or no language.
    """
    if media_id.source == "imdb":
        response = query_tmdb(search=media_id.value, api_key=api_key)
        results = response.get("movie_results") or response.get("tv_results") or []
        language = results[0].get("original_language") if results else None
    else:
        response = query_tmdb_by_id(
            tmdb_id=media_id.value, media_type=media_id.media_type, api_key=api_key
        )
        language = response.get("original_language")

    if not language:
        return None
    # TMDB uses 'cn' for Chinese, where ISO 639 uses 'zh'
    return "zh" if language == "cn" else language


def _query_arr_parse(service: str, search: str, *, url: str, api_key: str) -> dict[str, Any]:
    if not api_key or not url:
        return {}

    # The *arr v3 API accepts the key as a header, which keeps it out of URLs and error text
    return (
        _get_json(
            service,
            f"{url}/api/v3/parse",
            params={"title": search},
            headers={"X-Api-Key": api_key},
        )
        or {}
    )


def query_radarr(search: str, *, url: str, api_key: str) -> dict[str, Any]:
    """Ask Radarr to parse a movie title, to find the movie's IMDb ID.

    Args:
        search (str): Movie title to search for.
        url (str): The Radarr base URL. An empty URL skips the request.
        api_key (str): The Radarr API key. An empty key skips the request.

    Returns:
        dict: Radarr API response, or an empty dict when unavailable.
    """
    return _query_arr_parse(service="Radarr", search=search, url=url, api_key=api_key)


def query_sonarr(search: str, *, url: str, api_key: str) -> dict[str, Any]:
    """Ask Sonarr to parse a series title, to find the series' IMDb ID.

    Args:
        search (str): Series title to search for.
        url (str): The Sonarr base URL. An empty URL skips the request.
        api_key (str): The Sonarr API key. An empty key skips the request.

    Returns:
        dict: Sonarr API response, or an empty dict when unavailable.
    """
    return _query_arr_parse(service="Sonarr", search=search, url=url, api_key=api_key)


def _imdb_id_from_arr_response(response: dict[str, Any], media_key: str) -> str | None:
    """Read the IMDb ID from a Radarr or Sonarr parse response.

    An unmatched title can come back with the media object null or missing, and a match
    can lack an IMDb ID, so every level is checked before reading it.

    Args:
        response (dict[str, Any]): The parse response body.
        media_key (str): The key holding the matched media, `movie` or `series`.

    Returns:
        str | None: The IMDb ID, or None when the response has none.
    """
    media = response.get(media_key) if isinstance(response, dict) else None
    imdb_id = media.get("imdbId") if isinstance(media, dict) else None
    return imdb_id if isinstance(imdb_id, str) and imdb_id else None


def radarr_imdb_id(title: str, *, url: str, api_key: str) -> str | None:
    """Find the IMDb ID of the movie Radarr matches to a title.

    Args:
        title (str): The file name or title to match.
        url (str): The Radarr base URL. An empty URL skips the request.
        api_key (str): The Radarr API key. An empty key skips the request.

    Returns:
        str | None: The IMDb ID, or None when Radarr matches no movie or has no ID for it.
    """
    response = query_radarr(search=title, url=url, api_key=api_key)
    return _imdb_id_from_arr_response(response=response, media_key="movie")


def sonarr_imdb_id(title: str, *, url: str, api_key: str) -> str | None:
    """Find the IMDb ID of the series Sonarr matches to a title.

    Args:
        title (str): The file name or title to match.
        url (str): The Sonarr base URL. An empty URL skips the request.
        api_key (str): The Sonarr API key. An empty key skips the request.

    Returns:
        str | None: The IMDb ID, or None when Sonarr matches no series or has no ID for it.
    """
    response = query_sonarr(search=title, url=url, api_key=api_key)
    return _imdb_id_from_arr_response(response=response, media_key="series")
