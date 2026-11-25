"""Test API utilities."""

import httpx
import pytest
from nclutils import pp

from vid_cleaner.utils import (
    MediaId,
    query_radarr,
    query_sonarr,
    query_tmdb,
    query_tmdb_by_id,
    radarr_imdb_id,
    sonarr_imdb_id,
    tmdb_original_language,
)
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


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(f"connection refused for https://x/?api_key={SECRET}"),
        _status_error(),
    ],
    ids=["unreachable", "status"],
)
def test_tmdb_failure_is_reported_without_the_api_key(mocker, capsys, error):
    """Verify a failed query reports the service and returns no data instead of raising.

    A raised network error would escape the per-file error handling and stop a whole batch
    because one service is down.
    """
    pp.configure(verbosity=2)
    mocker.patch("vid_cleaner.utils.api_utils.httpx.get", side_effect=error)

    result = query_tmdb("tt1", api_key=SECRET)

    captured = capsys.readouterr()
    pp.configure(verbosity=0)
    assert result == {}
    assert "TMDB request failed" in captured.err
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


@pytest.mark.parametrize(
    ("lookup", "response", "expected"),
    [
        pytest.param(
            radarr_imdb_id,
            {"parsedMovieInfo": {"movieTitle": "Some Movie"}, "movie": {"imdbId": "tt0133093"}},
            "tt0133093",
            id="Radarr matched the movie",
        ),
        pytest.param(
            radarr_imdb_id,
            {"parsedMovieInfo": {"movieTitle": "Some Movie"}},
            None,
            id="Radarr parsed the title but matched no movie",
        ),
        pytest.param(
            radarr_imdb_id,
            {"movie": {"title": "Some Movie"}},
            None,
            id="Radarr matched a movie carrying no imdb id",
        ),
        pytest.param(
            radarr_imdb_id,
            {"parsedMovieInfo": {"movieTitle": "Some Movie"}, "movie": None},
            None,
            id="Radarr returned a null movie",
        ),
        pytest.param(
            sonarr_imdb_id,
            {"series": {"imdbId": "tt0903747"}},
            "tt0903747",
            id="Sonarr matched the series",
        ),
    ],
)
def test_arr_imdb_id_reads_parse_response(mocker, lookup, response, expected):
    """Verify the IMDb ID is read from a Radarr or Sonarr parse response without raising."""
    # Given: The service answers the parse request with this body
    mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=httpx.Response(
            status_code=200, json=response, request=httpx.Request("GET", "http://arr:1/")
        ),
    )

    # When: Looking up the IMDb ID for a title
    result = lookup("Some Movie (1999).mkv", url="http://arr:1", api_key=SECRET)

    # Then: The IMDb ID is returned when present, otherwise None
    assert result == expected


def test_tmdb_original_language_reads_tv_result_and_maps_chinese(mocker):
    """Verify an IMDb lookup falls back to TV results and maps TMDB's `cn` to ISO 639 `zh`."""
    # Given: TMDB finds the IMDb ID only as a series whose language is `cn`
    mocker.patch(
        "vid_cleaner.utils.api_utils.httpx.get",
        return_value=_response(
            status_code=200,
            payload={"movie_results": [], "tv_results": [{"original_language": "cn"}]},
        ),
    )

    # When: Looking up the original language
    result = tmdb_original_language(MediaId(source="imdb", value="tt0000001"), api_key=SECRET)

    # Then: The series language is returned as the ISO 639 code
    assert result == "zh"
