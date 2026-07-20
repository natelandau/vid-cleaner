# type: ignore
"""Test media ID discovery from filenames and container tags."""

from box import Box

from vid_cleaner.utils import MediaId, find_media_ids
from vid_cleaner.utils.media_ids import _dedupe


def test_finds_imdb_id_in_stem():
    """Verify a bare IMDb ID in the filename stem is found."""
    # Given: A stem carrying a bare IMDb ID
    stem = "Some Movie (1999) tt0133093 [Bluray-1080p]"

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=Box({}))

    # Then: The IMDb ID is returned
    assert result == [MediaId(source="imdb", value="tt0133093")]


def test_finds_tmdb_id_in_stem():
    """Verify the Radarr/Sonarr {tmdb-NN} convention is found in the stem."""
    # Given: A stem carrying a braced TMDB ID
    stem = "Amores Perros (2000) {tmdb-55} [Bluray-1080p]"

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=Box({}))

    # Then: The TMDB ID is returned with no media type
    assert result == [MediaId(source="tmdb", value="55", media_type=None)]


def test_finds_braced_imdb_id_in_stem():
    """Verify the {imdb-ttNN} naming convention is found in the stem."""
    # Given: A stem carrying a braced IMDb ID
    stem = "Some Movie (1999) {imdb-tt0133093}"

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=Box({}))

    # Then: The IMDb ID is returned without the brace prefix
    assert result == [MediaId(source="imdb", value="tt0133093")]


def test_finds_imdb_id_in_container_tags():
    """Verify an IMDB container tag is read when the stem has no ID."""
    # Given: A stem with no ID and a container IMDB tag
    stem = "Some Movie (1999)"
    tags = Box({"IMDB": "tt0245712"})

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=tags)

    # Then: The tag's IMDb ID is returned
    assert result == [MediaId(source="imdb", value="tt0245712")]


def test_finds_typed_tmdb_container_tag():
    """Verify a TMDB tag's movie/tv prefix is captured as the media type."""
    # Given: A container TMDB tag carrying its media type
    tags = Box({"TMDB": "movie/55"})

    # When: Media IDs are collected
    result = find_media_ids(stem="Some Movie (1999)", format_tags=tags)

    # Then: The media type is captured
    assert result == [MediaId(source="tmdb", value="55", media_type="movie")]


def test_finds_untyped_tmdb_container_tag():
    """Verify a bare numeric TMDB tag yields no media type."""
    # Given: A container TMDB tag with no media type prefix
    tags = Box({"TMDB": "55"})

    # When: Media IDs are collected
    result = find_media_ids(stem="Some Movie (1999)", format_tags=tags)

    # Then: The media type is None
    assert result == [MediaId(source="tmdb", value="55", media_type=None)]


def test_ignores_tvdb_tag():
    """Verify TVDB2 tags are not parsed, since TVDB is out of scope."""
    # Given: A container carrying only a TVDB2 tag
    tags = Box({"TVDB2": "movies/1982"})

    # When: Media IDs are collected
    result = find_media_ids(stem="Some Movie (1999)", format_tags=tags)

    # Then: Nothing is returned
    assert result == []


def test_orders_stem_before_tags_and_dedupes():
    """Verify stem IDs outrank container tags and duplicate IDs collapse."""
    # Given: A file carrying IDs in both the stem and the container
    stem = "Amores Perros (2000) {tmdb-55} [Bluray-1080p][AC3 5.1][x264]-ZoroSenpai"
    tags = Box({"IMDB": "tt0245712", "TMDB": "movie/55", "TVDB2": "movies/1982"})

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=tags)

    # Then: The stem's TMDB ID leads but is upgraded with the container's known
    # media type, the container's IMDb ID follows, and the duplicate is dropped
    assert result == [
        MediaId(source="tmdb", value="55", media_type="movie"),
        MediaId(source="imdb", value="tt0245712", media_type=None),
    ]


def test_untyped_stem_id_upgraded_by_typed_tag_keeps_position():
    """Verify a typed container duplicate upgrades an untyped stem ID in place."""
    # Given: An untyped TMDB ID in the stem and a typed duplicate in the container tags
    stem = "Amores Perros (2000) {tmdb-55}"
    tags = Box({"TMDB": "movie/55"})

    # When: Media IDs are collected
    result = find_media_ids(stem=stem, format_tags=tags)

    # Then: The single entry keeps its leading position but gains the media type
    assert result == [MediaId(source="tmdb", value="55", media_type="movie")]


def test_dedupe_does_not_downgrade_typed_entry():
    """Verify a typed entry is not overwritten by a later untyped duplicate.

    find_media_ids always discovers the untyped stem candidate before the (possibly
    typed) tag candidate, so this ordering cannot be produced through the public
    API. Exercise the dedup step directly instead.
    """
    # Given: A typed TMDB entry followed by an untyped duplicate
    candidates = [
        MediaId(source="tmdb", value="55", media_type="movie"),
        MediaId(source="tmdb", value="55", media_type=None),
    ]

    # When: Candidates are deduped
    result = _dedupe(candidates)

    # Then: The typed entry is kept, not overwritten by the untyped duplicate
    assert result == [MediaId(source="tmdb", value="55", media_type="movie")]


def test_returns_empty_when_nothing_found():
    """Verify a file with no discoverable ID yields an empty list."""
    # Given: A stem and tags with no IDs
    # When: Media IDs are collected
    result = find_media_ids(stem="Some Movie (1999)", format_tags=Box({}))

    # Then: Nothing is returned
    assert result == []
