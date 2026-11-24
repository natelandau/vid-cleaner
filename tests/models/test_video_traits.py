"""Test VideoFile trait derivation."""

import pytest
from iso639 import Lang

from vid_cleaner.constants import VideoTrait

from vid_cleaner.models.video_file import VideoFile  # isort: skip


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
    downmix_streams, _, _ = video._plan_downmix(streams=video.audio_streams, langs=[], force=False)  # noqa: SLF001

    # Then: the tag promises exactly what the planner delivers
    assert tagged == bool(downmix_streams)


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        pytest.param(
            "reference.json",
            {
                VideoTrait.H264,
                VideoTrait.FHD,
                VideoTrait.SURROUND7,
                VideoTrait.SURROUND5,
                VideoTrait.STEREO,
                VideoTrait.COMMENTARY,
            },
            id="1080p h264 with every surround layout and commentary",
        ),
        pytest.param(
            "uhd.json",
            {VideoTrait.H264, VideoTrait.UHDTV, VideoTrait.SURROUND5, VideoTrait.NEEDS_STEREO},
            id="4K with only a surround bed",
        ),
        pytest.param(
            "mono_only.json",
            {VideoTrait.H264, VideoTrait.FHD, VideoTrait.MONO},
            id="mono audio and a 1080-line height",
        ),
        pytest.param(
            "wrong_order.json",
            {
                VideoTrait.H264,
                VideoTrait.FHD,
                VideoTrait.SURROUND5,
                VideoTrait.STEREO,
                VideoTrait.REORDER,
            },
            id="streams out of order",
        ),
    ],
)
def test_get_traits_describes_each_file(make_video, fixture_name, expected):
    """Verify the traits name each file's codec, resolution, audio layouts, and stream order."""
    # Given: a probeable file
    video = make_video(fixture_name)

    # When: deriving traits
    traits = video.get_traits()

    # Then: exactly the expected traits are reported
    assert set(traits) == expected


@pytest.mark.parametrize(
    ("codec_name", "width", "height", "expected"),
    [
        pytest.param("hevc", 1920, 1080, {VideoTrait.H265, VideoTrait.FHD}, id="hevc"),
        pytest.param("vp9", 1920, 1080, {VideoTrait.H265, VideoTrait.FHD}, id="vp9 as modern"),
        pytest.param("h264", 1280, 720, {VideoTrait.H264, VideoTrait.HDTV}, id="720p"),
        pytest.param("h264", 720, 480, {VideoTrait.H264, VideoTrait.SDTV}, id="480p"),
        pytest.param("h264", 1920, 800, {VideoTrait.H264, VideoTrait.FHD}, id="letterboxed 1080p"),
        pytest.param(
            "mpeg2video", 1000, 500, {VideoTrait.UNKNOWN_RESOLUTION}, id="unknown codec and size"
        ),
    ],
)
def test_get_traits_video_codec_and_resolution(make_video, codec_name, width, height, expected):
    """Verify the video stream's codec and size map to codec and resolution traits."""
    # Given: a file whose only kept video stream has this codec and size
    video = make_video("mono_only.json")
    stream = video.probe_box.streams[0]
    stream.codec_name, stream.width, stream.height = codec_name, width, height

    # When: deriving traits
    traits = video.get_traits()

    # Then: only the expected video traits appear alongside the mono audio trait
    assert set(traits) - {VideoTrait.MONO} == expected
