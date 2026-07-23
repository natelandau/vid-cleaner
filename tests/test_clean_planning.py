# type: ignore
"""Test VideoFile single-pass conversion planning."""

from pathlib import Path

import pytest
from iso639 import Lang

from vid_cleaner import settings
from vid_cleaner.constants import CodecTypes

from vid_cleaner.models.video_file import VideoFile  # isort: skip


@pytest.fixture(autouse=True)
def set_default_settings(tmp_path, mocker):
    """Set default settings for planner tests."""
    cache_dir = Path(tmp_path) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings.update(
        {
            "cache_dir": cache_dir,
            "langs_to_keep": ["en"],
            "out_path": None,
            "downmix_stereo": False,
            "keep_local_subtitles": False,
            "keep_commentary": False,
            "drop_local_subs": False,
            "keep_all_subtitles": False,
            "drop_original_audio": False,
            "force": False,
            "h265": False,
            "vp9": False,
            "video_1080": False,
            "dryrun": False,
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


def test_build_plan_selects_streams_with_copy_codecs(make_video):
    """Verify the default plan maps kept streams in order with copy codecs."""
    # Given: reference.json (eng audio kept, French + commentary dropped, subs dropped)
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: kept streams are mapped video-first with copy codecs and no encodes
    assert [s.source_index for s in plan.streams] == [0, 1, 2, 4]
    assert all(s.codec == "copy" for s in plan.streams)
    assert plan.output_suffix is None
    assert plan.global_args == []


def test_build_plan_downmix_appends_encoded_stream(make_video):
    """Verify --downmix adds an AAC downmix stream after the kept audio."""
    # Given: no_stereo.json (7.1 at index 1, 5.1 at index 2, stereo commentary at 3)
    settings.update({"downmix_stereo": True})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: the 5.1 bed is mapped a second time as an AAC downmix with the filter chain
    downmix = plan.streams[-1]
    assert downmix.source_index == 2
    assert downmix.codec == "aac"
    assert downmix.stream_filter.startswith("pan=stereo|FL=0.8*FC")
    assert downmix.extra_args == ["-ac:a:{n}", "2", "-b:a:{n}", "256k", "-ar:a:{n}", "48000"]
    assert downmix.metadata == {"title": "2.0"}


def test_build_plan_h265_sets_video_codec_and_bitrates(make_video, tmp_path):
    """Verify --h265 encodes the video stream with size-derived bitrate options."""
    # Given: a 1 MB input file and reference.json probe data
    settings.update({"h265": True})
    video = make_video("reference.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: the video stream encodes to libx265 with bitrate args bound to it
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.extra_args[0] == "-b:v:{n}"
    assert "✔ Convert to H.265" in plan.substeps


def test_build_plan_h265_skips_when_already_h265(make_video):
    """Verify --h265 without --force leaves an already-H.265 video as a copy."""
    # Given: reference.json rewritten so the video codec is hevc
    settings.update({"h265": True})
    video = make_video("reference.json")
    video.probe_box.streams[0].codec_name = "hevc"

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: the video stream stays a copy and no H.265 substep is recorded
    assert plan.streams[0].codec == "copy"
    assert "✔ Convert to H.265" not in plan.substeps


def test_build_plan_1080p_scales_and_forces_encode(make_video):
    """Verify --1080p on a 4K source adds the scale filter and drops the copy codec."""
    # Given: a UHD source and --1080p
    settings.update({"video_1080": True})
    video = make_video("uhd.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: the video stream is scaled and left to the container default encoder
    video_stream = plan.streams[0]
    assert video_stream.stream_filter == "scale=width=1920:height=-2"
    assert video_stream.codec is None
    assert "✔ Convert to 1080p" in plan.substeps


def test_build_plan_1080p_and_h265_single_encode(make_video, tmp_path):
    """Verify --1080p --h265 yields one libx265 encode with the scale filter attached."""
    # Given: a UHD source with both conversion flags
    settings.update({"video_1080": True, "h265": True})
    video = make_video("uhd.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: one video stream carries both the codec and the filter
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.stream_filter == "scale=width=1920:height=-2"


def test_build_plan_vp9_converts_audio_and_text_subs(make_video):
    """Verify --vp9 encodes audio to libvorbis, converts text subs, and sets .webm."""
    # Given: a UHD source (subrip subtitle) with --vp9 and all subtitles kept
    settings.update({"vp9": True, "keep_all_subtitles": True, "out_path": Path("out.mkv")})
    video = make_video("uhd.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: video is VP9, audio is vorbis, the subrip sub becomes webvtt, container is webm
    assert plan.streams[0].codec == "libvpx-vp9"
    audio = [s for s in plan.streams if s.codec_type == CodecTypes.AUDIO]
    assert all(s.codec == "libvorbis" for s in audio)
    subs = [s for s in plan.streams if s.codec_type == CodecTypes.SUBTITLE]
    assert [s.codec for s in subs] == ["webvtt"]
    assert plan.output_suffix == ".webm"
    assert plan.global_args == ["-dn", "-map_chapters", "-1"]


def test_build_plan_vp9_drops_bitmap_subtitles(make_video):
    """Verify --vp9 drops image-based subtitles that WebM cannot carry."""
    # Given: reference.json (PGS subtitles) with --vp9 and all subtitles kept
    settings.update({"vp9": True, "keep_all_subtitles": True, "out_path": Path("out.mkv")})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: no subtitle stream survives into the WebM plan
    assert all(s.codec_type != CodecTypes.SUBTITLE for s in plan.streams)


def test_build_plan_vp9_downmix_uses_vorbis(make_video):
    """Verify a downmix track targets libvorbis, not AAC, when --vp9 is active."""
    # Given: a surround-only file with --downmix and --vp9
    settings.update({"vp9": True, "downmix_stereo": True, "out_path": Path("out.mkv")})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan()  # noqa: SLF001

    # Then: the appended downmix stream encodes with vorbis for WebM compatibility
    downmix = plan.streams[-1]
    assert downmix.stream_filter is not None
    assert downmix.codec == "libvorbis"


def test_estimate_cleaned_size_subtracts_dropped_streams(make_video):
    """Verify the size estimate subtracts dropped streams' bitrate-derived bytes."""
    # Given: a 10 MB file where a dropped 640 kbps stream spans 60 seconds
    video = make_video("reference.json")
    video.temp_file.path.write_bytes(b"0" * 10_000_000)
    video.probe_box.streams[3].bit_rate = "640000"
    for stream in video.probe_box.streams:
        if stream.index != 3:
            stream.bit_rate = None
            stream.bps = None

    # When: estimating with only stream 3 dropped
    mapped = {s.index for s in video.probe_box.streams if s.index != 3}
    size_mb = video._estimate_cleaned_size_mb(mapped_indices=mapped, duration=60.0)  # noqa: SLF001

    # Then: 640000/8 * 60 = 4.8 MB is subtracted from the 10 MB file
    assert size_mb == pytest.approx(5.2, rel=0.01)


def test_clean_noop_skips_ffmpeg(make_video, mock_ffmpeg):
    """Verify a plan that changes nothing skips ffmpeg and reports no work."""
    # Given: a file whose only audio has an unmapped channel count and --downmix
    settings.update({"downmix_stereo": True})
    video = make_video("unmapped_channels.json")

    # When: cleaning
    substeps = video.clean()

    # Then: ffmpeg never runs and the no-op substeps are returned
    mock_ffmpeg.assert_not_called()
    assert any("No streams to process" in step for step in substeps)
