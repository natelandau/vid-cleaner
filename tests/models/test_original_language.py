"""Test how VideoFile finds a video's original language.

These mock at the TMDB, Radarr, and Sonarr query boundary so the real ID discovery
and fallback order run.
"""

import pytest
from iso639 import Lang

from vid_cleaner.models.video_file import VideoFile  # isort: skip


def test_find_original_language_skips_arr_when_filename_id_resolves(
    mocker,
    tmp_path,
    mock_probe_tags,
    make_config,
) -> None:
    """Verify Radarr/Sonarr are not queried once a filename-derived ID resolves."""
    # Given: a filename carrying a resolvable IMDb ID and no container tags
    path = tmp_path / "Some Movie (1999) tt0133093.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb",
        autospec=True,
        return_value={"movie_results": [{"original_language": "en"}]},
    )
    mock_radarr = mocker.patch("vid_cleaner.models.video_file.radarr_imdb_id")
    mock_sonarr = mocker.patch("vid_cleaner.models.video_file.sonarr_imdb_id")

    # When: finding the original language
    result = video_file._find_original_language(make_config(), notes=[])  # noqa: SLF001

    # Then: the filename's IMDb ID resolves the language and Radarr/Sonarr are never queried
    assert result == Lang("en")
    mock_query_tmdb.assert_called_once_with("tt0133093", api_key=mocker.ANY)
    mock_radarr.assert_not_called()
    mock_sonarr.assert_not_called()


@pytest.mark.parametrize(
    ("radarr_answer", "sonarr_answer"),
    [
        pytest.param("tt0245712", None, id="Radarr knows the movie"),
        pytest.param(None, "tt0245712", id="Sonarr knows the show"),
    ],
)
def test_find_original_language_consults_arr_when_no_id_resolves(
    mocker, tmp_path, mock_probe_tags, make_config, radarr_answer, sonarr_answer
) -> None:
    """Verify Radarr, then Sonarr, supply the IMDb ID when no ID is discoverable."""
    # Given: a filename and container with no discoverable media ID
    path = tmp_path / "Some Title (1999).mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb",
        autospec=True,
        return_value={"movie_results": [{"original_language": "fr"}]},
    )
    mocker.patch("vid_cleaner.models.video_file.radarr_imdb_id", return_value=radarr_answer)
    mocker.patch("vid_cleaner.models.video_file.sonarr_imdb_id", return_value=sonarr_answer)

    # When: finding the original language
    result = video_file._find_original_language(make_config(), notes=[])  # noqa: SLF001

    # Then: the IMDb ID from whichever service knew the title resolves the language
    assert result == Lang("fr")
    mock_query_tmdb.assert_called_once_with("tt0245712", api_key=mocker.ANY)


def test_find_original_language_caches_result(
    mocker, tmp_path, mock_probe_tags, make_config
) -> None:
    """Verify a second call reuses the cached language instead of re-querying."""
    # Given: a filename with a resolvable IMDb ID
    path = tmp_path / "Some Movie (1999) tt0133093.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb",
        autospec=True,
        return_value={"movie_results": [{"original_language": "en"}]},
    )

    # When: finding the original language twice
    first = video_file._find_original_language(make_config(), notes=[])  # noqa: SLF001
    second = video_file._find_original_language(make_config(), notes=[])  # noqa: SLF001

    # Then: both calls return the cached language but only one query was made
    assert first == Lang("en")
    assert second == Lang("en")
    mock_query_tmdb.assert_called_once()


def test_find_original_language_falls_through_failed_candidate(
    mocker,
    tmp_path,
    mock_probe_tags,
    make_config,
) -> None:
    """Verify a failed first candidate falls through to the next discovered ID."""
    # Given: a filename IMDb ID that fails to resolve and a container TMDB tag that does
    path = tmp_path / "Some Movie (1999) tt9999999.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags({"TMDB": "movie/1399"})
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb", autospec=True, return_value={}
    )
    mock_query_tmdb_by_id = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb_by_id",
        autospec=True,
        return_value={"original_language": "en"},
    )

    # When: finding the original language
    result = video_file._find_original_language(make_config(), notes=[])  # noqa: SLF001

    # Then: the unresolvable IMDb candidate is skipped and the TMDB candidate resolves
    assert result == Lang("en")
    mock_query_tmdb.assert_called_once_with("tt9999999", api_key=mocker.ANY)
    mock_query_tmdb_by_id.assert_called_once_with(
        tmdb_id="1399", media_type="movie", api_key=mocker.ANY
    )


def test_find_original_language_notes_unusable_tmdb_language(
    mocker, tmp_path, mock_probe_tags, make_config
) -> None:
    """Verify a TMDB language that is not an ISO 639 code is noted and treated as unknown."""
    # Given: a filename IMDb ID whose TMDB record carries an unusable language code
    path = tmp_path / "Some Movie (1999) tt0133093.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb",
        autospec=True,
        return_value={"movie_results": [{"original_language": "xx"}]},
    )
    mocker.patch("vid_cleaner.models.video_file.radarr_imdb_id", return_value=None)
    mocker.patch("vid_cleaner.models.video_file.sonarr_imdb_id", return_value=None)
    notes: list[str] = []

    # When: finding the original language
    result = video_file._find_original_language(config=make_config(), notes=notes)  # noqa: SLF001

    # Then: no language is found and the debug notes name the unusable code
    assert result is None
    assert any("'xx'" in note for note in notes)


def test_find_original_language_skips_arr_id_already_tried(
    mocker, tmp_path, mock_probe_tags, make_config
) -> None:
    """Verify an IMDb ID from Radarr that already failed to resolve is not queried again."""
    # Given: a filename IMDb ID that TMDB cannot resolve, and Radarr returning that same ID
    path = tmp_path / "Some Movie (1999) tt9999999.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.utils.api_utils.query_tmdb", autospec=True, return_value={}
    )
    mocker.patch("vid_cleaner.models.video_file.radarr_imdb_id", return_value="tt9999999")

    # When: finding the original language
    result = video_file._find_original_language(config=make_config(), notes=[])  # noqa: SLF001

    # Then: TMDB was asked about the ID only once
    assert result is None
    mock_query_tmdb.assert_called_once_with("tt9999999", api_key=mocker.ANY)
