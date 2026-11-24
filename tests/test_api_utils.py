"""Test API utilities."""

import httpx
import pytest
from nclutils import pp

from vid_cleaner.utils import query_radarr, query_sonarr, query_tmdb, query_tmdb_by_id
from vid_cleaner.utils.api_utils import describe_http_error

SECRET = "SECRET-KEY-123"  # noqa: S105


def _response(status_code: int, payload: dict[str, object]) -> httpx.Response:
    """Build an httpx response bound to a request, as raise_for_status requires."""
    return httpx.Response(
        status_code=status_code,
        json=payload,
        request=httpx.Request("GET", "https://api.themoviedb.org/"),
    )


def test_queries_movie_endpoint_when_type_known(mocker):
    """Verify a known media type queries only that endpoint."""
    # Given: A movie record is available
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=_response(status_code=200, payload={"original_language": "es"}),
    )

    # When: A typed lookup runs
    result = query_tmdb_by_id(tmdb_id="55", media_type="movie", api_key="test-key")

    # Then: The movie endpoint is queried exactly once
    assert result == {"original_language": "es"}
    assert get.call_count == 1
    assert get.call_args[0][0] == "https://api.themoviedb.org/3/movie/55"


def test_falls_back_to_tv_when_movie_missing(mocker):
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
    result = query_tmdb_by_id(tmdb_id="1399", api_key="test-key")

    # Then: Both endpoints are tried in order and the tv record is returned
    assert result == {"original_language": "ja"}
    assert get.call_count == 2
    assert get.call_args_list[0][0][0] == "https://api.themoviedb.org/3/movie/1399"
    assert get.call_args_list[1][0][0] == "https://api.themoviedb.org/3/tv/1399"


def test_returns_empty_when_both_endpoints_miss(mocker):
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
    result = query_tmdb_by_id(tmdb_id="999999999", api_key="test-key")

    # Then: Both endpoints are tried and nothing is returned
    assert result == {}
    assert get.call_count == 2
    assert get.call_args_list[0][0][0] == "https://api.themoviedb.org/3/movie/999999999"
    assert get.call_args_list[1][0][0] == "https://api.themoviedb.org/3/tv/999999999"


def test_stops_on_non_404_error(mocker):
    """Verify a server error aborts rather than falling through to the tv endpoint."""
    # Given: The movie endpoint returns a server error
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=_response(status_code=500, payload={"status_message": "server error"}),
    )

    # When: An untyped lookup runs
    result = query_tmdb_by_id(tmdb_id="55", api_key="test-key")

    # Then: It gives up without trying tv, since the error is not a type mismatch
    assert result == {}
    assert get.call_count == 1


@pytest.mark.parametrize(
    "query",
    [
        lambda: query_tmdb("tt0111161", api_key=""),
        lambda: query_tmdb_by_id(tmdb_id="55", api_key=""),
        lambda: query_radarr("Movie", url="http://radarr:7878", api_key=""),
        lambda: query_sonarr("Show", url="", api_key="key"),
    ],
    ids=["tmdb", "tmdb_by_id", "radarr_no_key", "sonarr_no_url"],
)
def test_missing_credentials_skip_the_request(mocker, query):
    """Verify no request is made when a service is not configured."""
    get = mocker.patch("vid_cleaner.utils.api_utils.httpx.get")

    assert query() == {}
    get.assert_not_called()


def _status_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.themoviedb.org/3/find/tt1?api_key={SECRET}")
    response = httpx.Response(status_code=401, request=request)
    return httpx.HTTPStatusError(f"401 for url {request.url}", request=request, response=response)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout(f"timed out for https://x/?api_key={SECRET}"),
        _status_error(),
    ],
    ids=["timeout", "status"],
)
def test_http_error_message_hides_api_key(error):
    """Verify a request failure is described without its URL or key."""
    message = describe_http_error("TMDB", error)

    assert message.startswith("TMDB request failed")
    assert SECRET not in message


def test_tmdb_failure_output_hides_api_key(mocker, capsys):
    """Verify neither the trace nor the error printed for a failed query shows the key."""
    pp.configure(verbosity=2)
    mocker.patch("vid_cleaner.utils.api_utils.httpx.get", side_effect=_status_error())

    query_tmdb("tt1", api_key=SECRET)

    captured = capsys.readouterr()
    pp.configure(verbosity=0)
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("query", [query_radarr, query_sonarr])
def test_arr_query_sends_key_in_header(mocker, query):
    """Verify Radarr and Sonarr keys travel in a header, never in the URL."""
    get = mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=httpx.Response(
            status_code=200, json={}, request=httpx.Request("GET", "http://arr:1/")
        ),
    )

    query("Movie", url="http://arr:1", api_key=SECRET)

    assert get.call_args.kwargs["headers"] == {"X-Api-Key": SECRET}
    assert SECRET not in str(get.call_args.kwargs["params"])


def test_non_json_response_returns_empty(mocker):
    """Verify a 200 response that is not JSON is reported and treated as no result."""
    mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=httpx.Response(
            status_code=200,
            text="<html>login</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://arr:1/"),
        ),
    )

    assert query_radarr("Movie", url="http://arr:1", api_key=SECRET) == {}
