# type: ignore
"""Test API utilities."""

import httpx
import pytest

from vid_cleaner import settings
from vid_cleaner.utils import query_tmdb_by_id


@pytest.fixture
def tmdb_key():
    """Provide a TMDB API key so the query is not short-circuited."""
    # Dynaconf settings aren't real attributes, so mocker.patch.object's teardown
    # deletes rather than restores them; save and restore the value by hand.
    previous = settings.get("TMDB_API_KEY", "")
    settings.set("TMDB_API_KEY", "test-key")
    yield
    settings.set("TMDB_API_KEY", previous)


@pytest.fixture
def tmdb_no_key():
    """Clear the TMDB API key so the query is short-circuited."""
    previous = settings.get("TMDB_API_KEY", "")
    settings.set("TMDB_API_KEY", "")
    yield
    settings.set("TMDB_API_KEY", previous)


def _response(status_code: int, payload: dict) -> httpx.Response:
    """Build an httpx response bound to a request, as raise_for_status requires."""
    return httpx.Response(
        status_code=status_code,
        json=payload,
        request=httpx.Request("GET", "https://api.themoviedb.org/"),
    )


def test_returns_empty_without_api_key(tmdb_no_key, mocker):
    """Verify no request is made when no API key is configured."""
    # Given: No TMDB API key
    get = mocker.patch("vid_cleaner.utils.api_utils.httpx.get")

    # When: A lookup is attempted
    result = query_tmdb_by_id(tmdb_id="55")

    # Then: Nothing is requested and an empty dict comes back
    assert result == {}
    get.assert_not_called()


def test_queries_movie_endpoint_when_type_known(tmdb_key, mocker):
    """Verify a known media type queries only that endpoint."""
    # Given: A movie record is available
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=_response(status_code=200, payload={"original_language": "es"}),
    )

    # When: A typed lookup runs
    result = query_tmdb_by_id(tmdb_id="55", media_type="movie")

    # Then: The movie endpoint is queried exactly once
    assert result == {"original_language": "es"}
    assert get.call_count == 1
    assert get.call_args[0][0] == "https://api.themoviedb.org/3/movie/55"


def test_falls_back_to_tv_when_movie_missing(tmdb_key, mocker):
    """Verify an untyped lookup retries against tv after the movie endpoint 404s."""
    # Given: The ID is a tv record, so the movie endpoint 404s
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        side_effect=[
            _response(status_code=404, payload={"status_message": "not found"}),
            _response(status_code=200, payload={"original_language": "ja"}),
        ],
    )

    # When: An untyped lookup runs
    result = query_tmdb_by_id(tmdb_id="1399")

    # Then: Both endpoints are tried in order and the tv record is returned
    assert result == {"original_language": "ja"}
    assert get.call_count == 2
    assert get.call_args_list[0][0][0] == "https://api.themoviedb.org/3/movie/1399"
    assert get.call_args_list[1][0][0] == "https://api.themoviedb.org/3/tv/1399"


def test_returns_empty_when_both_endpoints_miss(tmdb_key, mocker):
    """Verify an ID found in neither endpoint yields an empty dict."""
    # Given: Both endpoints 404
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        side_effect=[
            _response(status_code=404, payload={"status_message": "not found"}),
            _response(status_code=404, payload={"status_message": "not found"}),
        ],
    )

    # When: An untyped lookup runs
    result = query_tmdb_by_id(tmdb_id="999999999")

    # Then: Both endpoints are tried and nothing is returned
    assert result == {}
    assert get.call_count == 2
    assert get.call_args_list[0][0][0] == "https://api.themoviedb.org/3/movie/999999999"
    assert get.call_args_list[1][0][0] == "https://api.themoviedb.org/3/tv/999999999"


def test_stops_on_non_404_error(tmdb_key, mocker):
    """Verify a server error aborts rather than falling through to the tv endpoint."""
    # Given: The movie endpoint returns a server error
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=_response(status_code=500, payload={"status_message": "server error"}),
    )

    # When: An untyped lookup runs
    result = query_tmdb_by_id(tmdb_id="55")

    # Then: It gives up without trying tv, since the error is not a type mismatch
    assert result == {}
    assert get.call_count == 1
