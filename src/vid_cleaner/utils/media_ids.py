"""Discover media IDs from filenames and container metadata."""

import re
from dataclasses import dataclass
from typing import Literal, cast

from box import Box

IMDB_ID_REGEX = re.compile(r"(tt\d+)")
TMDB_STEM_REGEX = re.compile(r"tmdb(?:id)?-(\d+)", re.IGNORECASE)
TMDB_TAG_REGEX = re.compile(r"(?:(movie|tv)/)?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class MediaId:
    """A media database identifier discovered for a video file."""

    source: Literal["imdb", "tmdb"]
    value: str
    media_type: Literal["movie", "tv"] | None = None


def find_media_ids(stem: str, format_tags: Box) -> list[MediaId]:
    """Collect every media ID discoverable from a filename stem and container tags.

    Prefer IDs embedded in the filename over the container's tags, since the filename
    reflects how the user's media manager named the file while container tags come from
    whoever built the release and may be stale.

    Args:
        stem: The video file's name without its suffix.
        format_tags: The container's `format.tags` mapping from ffprobe.

    Returns:
        list[MediaId]: Ordered, deduplicated IDs. Empty when none are found.
    """
    candidates: list[MediaId] = []

    if match := IMDB_ID_REGEX.search(stem):
        candidates.append(MediaId(source="imdb", value=match.group(1)))

    if match := TMDB_STEM_REGEX.search(stem):
        candidates.append(MediaId(source="tmdb", value=match.group(1)))

    tags = {str(key).lower(): str(value) for key, value in (format_tags or {}).items()}

    if (imdb_tag := tags.get("imdb")) and (match := IMDB_ID_REGEX.search(imdb_tag)):
        candidates.append(MediaId(source="imdb", value=match.group(1)))

    if (tmdb_tag := tags.get("tmdb")) and (match := TMDB_TAG_REGEX.search(tmdb_tag)):
        # The regex only ever captures "movie", "tv", or nothing, but mypy can't see that.
        media_type = (
            cast("Literal['movie', 'tv']", match.group(1).lower()) if match.group(1) else None
        )
        candidates.append(MediaId(source="tmdb", value=match.group(2), media_type=media_type))

    return _dedupe(candidates)


def _dedupe(candidates: list[MediaId]) -> list[MediaId]:
    """Collapse duplicate (source, value) IDs, keeping first position but preferring a typed one.

    TMDB's movie and TV ID sequences are separate and both start at 1, so an untyped
    ID may collide with an unrelated typed one. Letting a later typed duplicate
    upgrade an earlier untyped entry avoids querying the wrong TMDB namespace.

    Args:
        candidates: Media IDs in discovery order, possibly containing duplicates.

    Returns:
        list[MediaId]: Deduplicated IDs, preserving each kept entry's original position.
    """
    seen: dict[tuple[str, str], int] = {}
    deduped: list[MediaId] = []
    for candidate in candidates:
        key = (candidate.source, candidate.value)
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(candidate)
        elif candidate.media_type and not deduped[seen[key]].media_type:
            # movie and tv IDs collide, so a known type must win over an untyped duplicate
            deduped[seen[key]] = candidate

    return deduped
