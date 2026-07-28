# type: ignore
"""Test VideoFile trait derivation."""

from pathlib import Path

import pytest
from iso639 import Lang

from vid_cleaner import settings
from vid_cleaner.constants import VideoTrait

from vid_cleaner.models.video_file import VideoFile  # isort: skip


@pytest.fixture(autouse=True)
def set_default_settings(tmp_path):
    """Set default settings for trait tests."""
    cache_dir = Path(tmp_path) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings.update(
        {
            "cache_dir": cache_dir,
            "langs_to_keep": ["en"],
            "keep_commentary": False,
            "force": False,
        }
    )


@pytest.fixture
def make_video(mocker, mock_video_path, mock_ffprobe_box):
    """Build a VideoFile whose probe data comes from a named ffprobe fixture."""

    def _inner(fixture_name: str, language: str = "en") -> VideoFile:
        mocker.patch(
            "vid_cleaner.models.video_file.get_probe_as_box",
            return_value=mock_ffprobe_box(fixture_name),
        )
        mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang(language))
        return VideoFile(mock_video_path)

    return _inner


DOWNMIXABLE_FIXTURES = [
    "no_stereo.json",
    "atmos_no_stereo.json",
]

NOT_DOWNMIXABLE_FIXTURES = [
    "mono_with_commentary.json",
    "mono_only.json",
    "stereo_no_surround.json",
    "two_stereo_and_surround.json",
    "unmapped_channels.json",
]


@pytest.mark.parametrize("fixture_name", DOWNMIXABLE_FIXTURES)
def test_get_traits_needs_stereo_when_surround_bed_available(make_video, fixture_name):
    """Verify needs_stereo is tagged when no stereo mix exists but a surround bed does."""
    # Given: a file with a surround bed and no non-commentary stereo stream
    video = make_video(fixture_name)

    # When: deriving traits
    traits = video.get_traits()

    # Then: the file is flagged as needing a stereo track
    assert VideoTrait.NEEDS_STEREO in traits


@pytest.mark.parametrize("fixture_name", NOT_DOWNMIXABLE_FIXTURES)
def test_get_traits_no_needs_stereo_without_downmix_source(make_video, fixture_name):
    """Verify needs_stereo is withheld when no stereo track could be created."""
    # Given: a file that either already has a stereo mix or has no surround bed
    video = make_video(fixture_name)

    # When: deriving traits
    traits = video.get_traits()

    # Then: the file is not flagged
    assert VideoTrait.NEEDS_STEREO not in traits


def test_get_traits_ignores_commentary_when_looking_for_a_stereo_mix(make_video):
    """Verify a stereo commentary track does not count as an existing stereo mix."""
    # Given: 7.1 + 5.1 beds whose only stereo stream is a commentary track
    video = make_video("no_stereo.json")

    # When: deriving traits
    traits = video.get_traits()

    # Then: the commentary track is reported separately and does not suppress the flag
    assert VideoTrait.COMMENTARY in traits
    assert VideoTrait.NEEDS_STEREO in traits


@pytest.mark.parametrize(
    "fixture_name", DOWNMIXABLE_FIXTURES + NOT_DOWNMIXABLE_FIXTURES + ["reference.json"]
)
def test_get_traits_needs_stereo_agrees_with_downmix_planner(make_video, fixture_name):
    """Verify the needs_stereo tag matches whether the planner would build a stereo track."""
    # Given: any probeable file
    video = make_video(fixture_name)

    # When: comparing the trait against what the downmix planner would produce
    tagged = VideoTrait.NEEDS_STEREO in video.get_traits()
    downmix_streams, _, _ = video._plan_downmix(video.audio_streams)  # noqa: SLF001

    # Then: the tag promises exactly what the planner delivers
    assert tagged == bool(downmix_streams)
