"""Test VideoFile single-pass conversion planning."""

from dataclasses import fields
from typing import Any

import pytest
from box import Box

from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import (
    ALL_AUDIO_KEPT_REASON,
    ALREADY_MODERN_CODEC_REASON,
    CONVERT_TO_H265_LABEL,
    CONVERT_TO_VP9_LABEL,
    DOWNMIX_STEREO_LABEL,
    DROP_AUDIO_LABEL,
    DROP_SUBTITLES_LABEL,
    KEEP_ALL_SUBTITLES_REASON,
    NO_AUDIO_STREAMS_REASON,
    NO_SUBTITLES_REASON,
    NO_SURROUND_SOURCE_REASON,
    NO_UNWANTED_SUBTITLES_REASON,
    NO_VIDEO_STREAMS_REASON,
    SCALE_TO_1080P_LABEL,
    SILENT_FILE_REASON,
    UNKNOWN_DURATION_REASON,
    CodecTypes,
)
from vid_cleaner.exceptions import VideoCleanError

from vid_cleaner.cli.clean_video import clean_video_file  # isort: skip


@pytest.fixture
def plan_settings(make_config):
    """Collect per-test setting overrides and build the planner's inputs from them.

    Returns:
        PlanSettings: Call `update` with setting names, then pass `args()` to the planner.
    """
    config_fields = {field.name for field in fields(Config)}

    class PlanSettings:
        def __init__(self) -> None:
            self.values: dict[str, Any] = {}

        def update(self, values: dict[str, Any]) -> None:
            self.values.update(values)

        def args(self) -> tuple[Config, RunOptions]:
            config_values = {k: v for k, v in self.values.items() if k in config_fields}
            if "langs_to_keep" in config_values:
                config_values["langs_to_keep"] = tuple(config_values["langs_to_keep"])
            option_values = {k: v for k, v in self.values.items() if k not in config_fields}
            return make_config(**config_values), RunOptions(**option_values)

    return PlanSettings()


def test_build_plan_selects_streams_with_copy_codecs(make_video, plan_settings):
    """Verify the default plan maps kept streams in order with copy codecs."""
    # Given: reference.json (eng audio kept, French + commentary dropped, subs dropped)
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: kept streams are mapped video-first with copy codecs and no encodes
    assert [s.source_index for s in plan.streams] == [0, 1, 2, 4]
    assert all(s.codec == "copy" for s in plan.streams)
    assert plan.output_suffix is None
    assert plan.global_args == []


def test_build_plan_downmix_appends_encoded_stream(make_video, plan_settings):
    """Verify --downmix adds an AAC downmix stream after the kept audio."""
    # Given: no_stereo.json (7.1 at index 1, 5.1 at index 2, stereo commentary at 3)
    plan_settings.update({"downmix_stereo": True})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the 5.1 bed is mapped a second time as an AAC downmix with the filter chain
    downmix = plan.streams[-1]
    assert downmix.source_index == 2
    assert downmix.codec == "aac"
    assert downmix.stream_filter.startswith("pan=stereo|FL=0.7*FC")
    assert downmix.extra_args == ["-ac:a:{n}", "2", "-b:a:{n}", "256k", "-ar:a:{n}", "48000"]
    assert downmix.metadata == {"title": "2.0"}


def test_build_plan_h265_sets_video_codec_and_bitrates(make_video, plan_settings):
    """Verify --h265 encodes the video stream with size-derived bitrate options."""
    # Given: a 1 MB input file and reference.json probe data
    plan_settings.update({"h265": True})
    video = make_video("reference.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the video stream encodes to libx265 with bitrate args bound to it
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.extra_args[0] == "-b:v:{n}"
    h265_action = next(a for a in plan.actions if a.label == CONVERT_TO_H265_LABEL)
    assert h265_action.applied is True


def test_h265_records_applied_action(make_video, plan_settings):
    """Verify --h265 records an applied action when the encode runs."""
    # Given: reference.json (h264 video) with --h265 and a 1 MB input file
    plan_settings.update({"h265": True})
    video = make_video("reference.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the recorded action is applied
    h265_action = next(a for a in plan.actions if a.label == CONVERT_TO_H265_LABEL)
    assert h265_action.applied is True


@pytest.mark.parametrize(
    ("flag", "label"),
    [
        pytest.param("h265", CONVERT_TO_H265_LABEL, id="h265"),
        pytest.param("vp9", CONVERT_TO_VP9_LABEL, id="vp9"),
    ],
)
def test_codec_conversion_records_skip_reason_when_already_modern(
    make_video, plan_settings, flag, label
):
    """Verify --h265 or --vp9 without --force skips an already-H.265 video and names --force."""
    # Given: reference.json rewritten so the video codec is hevc
    plan_settings.update({flag: True})
    video = make_video("reference.json")
    video.probe_box.streams[0].codec_name = "hevc"

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the recorded action is skipped with a reason pointing to --force, and the
    # video and container are left alone
    action = next(a for a in plan.actions if a.label == label)
    assert action.applied is False
    assert action.reason == ALREADY_MODERN_CODEC_REASON
    assert plan.streams[0].codec == "copy"
    assert plan.output_suffix is None


def test_h265_skips_when_duration_unknown(make_video, plan_settings):
    """Verify --h265 skips the encode when no duration is available to size the bitrate."""
    # Given: reference.json with no container or stream duration, and --h265
    plan_settings.update({"h265": True})
    video = make_video("reference.json")
    video.probe_box.duration = None
    video.probe_box.streams[0].duration = None

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the video stays a copy and the skip says why
    action = next(a for a in plan.actions if a.label == CONVERT_TO_H265_LABEL)
    assert action.applied is False
    assert action.reason == UNKNOWN_DURATION_REASON
    assert plan.streams[0].codec == "copy"


def test_build_plan_1080p_scales_and_forces_encode(make_video, plan_settings):
    """Verify --1080p on a 4K source adds the scale filter and drops the copy codec."""
    # Given: a UHD source and --1080p
    plan_settings.update({"video_1080": True})
    video = make_video("uhd.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the video stream is scaled and left to the container default encoder
    video_stream = plan.streams[0]
    assert video_stream.stream_filter == "scale=width=1920:height=-2"
    assert video_stream.codec is None
    scale_action = next(a for a in plan.actions if a.label == SCALE_TO_1080P_LABEL)
    assert scale_action.applied is True


def test_scale_records_skip_reason_when_already_1080p(make_video, plan_settings):
    """Verify --1080p on an already-1080p source records a skip reason."""
    # Given: reference.json (1080p H264 video) and --1080p
    plan_settings.update({"video_1080": True})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the recorded action is skipped with a reason naming 1080p
    scale_action = next(a for a in plan.actions if a.label == SCALE_TO_1080P_LABEL)
    assert scale_action.applied is False
    assert scale_action.reason is not None
    assert "1080" in scale_action.reason


def test_build_plan_h265_encodes_every_video_stream(make_video, plan_settings):
    """Verify --h265 encodes all video streams, not just the first."""
    # Given: a file with two real (non-thumbnail) video streams and --h265
    plan_settings.update({"h265": True})
    video = make_video("multi_video.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: both video streams are encoded to libx265 with bitrate args, none left as a copy
    video_streams = [s for s in plan.streams if s.codec_type == CodecTypes.VIDEO]
    assert len(video_streams) == 2
    assert all(s.codec == "libx265" for s in video_streams)
    assert all(s.extra_args and s.extra_args[0] == "-b:v:{n}" for s in video_streams)


def test_build_plan_1080p_scales_every_video_stream(make_video, plan_settings):
    """Verify --1080p scales all video streams, not just the first."""
    # Given: a file with two 4K video streams and --1080p
    plan_settings.update({"video_1080": True})
    video = make_video("multi_video.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: both video streams carry the scale filter and drop the copy codec
    video_streams = [s for s in plan.streams if s.codec_type == CodecTypes.VIDEO]
    assert len(video_streams) == 2
    assert all(s.stream_filter == "scale=width=1920:height=-2" for s in video_streams)
    assert all(s.codec is None for s in video_streams)


def test_build_plan_1080p_and_h265_single_encode(make_video, plan_settings):
    """Verify --1080p --h265 yields one libx265 encode with the scale filter attached."""
    # Given: a UHD source with both conversion flags
    plan_settings.update({"video_1080": True, "h265": True})
    video = make_video("uhd.json")
    video.temp_file.path.write_bytes(b"0" * 1_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: one video stream carries both the codec and the filter
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.stream_filter == "scale=width=1920:height=-2"


def test_build_plan_h265_bitrate_math(make_video, plan_settings):
    """Verify the H.265 bitrate targets are computed from the on-disk file size."""
    # Given: a 600 MB, 4K/600s source with only --h265 (no scale, kept video+audio)
    plan_settings.update({"h265": True})
    video = make_video("uhd.json")
    video.temp_file.path.write_bytes(b"0" * 600_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: bitrates follow the Frame.io formula with no pixel-ratio scaling
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.extra_args == [
        "-b:v:{n}",
        "3999k",
        "-minrate:v:{n}",
        "5599k",
        "-maxrate:v:{n}",
        "10398k",
        "-bufsize:v:{n}",
        "7999k",
    ]


def test_build_plan_h265_bitrate_math_scaled_for_1080p(make_video, plan_settings):
    """Verify --1080p shrinks the H.265 bitrate targets by the pixel ratio."""
    # Given: the same 600 MB, 4K/600s source, now also downscaling to 1080p
    plan_settings.update({"h265": True, "video_1080": True})
    video = make_video("uhd.json")
    video.temp_file.path.write_bytes(b"0" * 600_000_000)

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the bitrates are quartered (scale_factor = (1920/3840)**2 = 0.25) and the
    # scale filter still lands on the same combined video stream
    video_stream = plan.streams[0]
    assert video_stream.codec == "libx265"
    assert video_stream.stream_filter == "scale=width=1920:height=-2"
    assert video_stream.extra_args == [
        "-b:v:{n}",
        "999k",
        "-minrate:v:{n}",
        "1399k",
        "-maxrate:v:{n}",
        "2598k",
        "-bufsize:v:{n}",
        "1999k",
    ]


def test_build_plan_vp9_converts_audio_and_text_subs(make_video, plan_settings):
    """Verify --vp9 encodes audio to libvorbis, converts text subs, and sets .webm."""
    # Given: a UHD source (subrip subtitle) with --vp9 and all subtitles kept
    plan_settings.update({"vp9": True, "keep_all_subtitles": True})
    video = make_video("uhd.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: video is VP9, audio is vorbis, the subrip sub becomes webvtt, container is webm
    assert plan.streams[0].codec == "libvpx-vp9"
    audio = [s for s in plan.streams if s.codec_type == CodecTypes.AUDIO]
    assert all(s.codec == "libvorbis" for s in audio)
    subs = [s for s in plan.streams if s.codec_type == CodecTypes.SUBTITLE]
    assert [s.codec for s in subs] == ["webvtt"]
    assert plan.output_suffix == ".webm"
    assert plan.global_args == ["-dn", "-map_chapters", "-1"]


def test_build_plan_vp9_drops_bitmap_subtitles(make_video, plan_settings):
    """Verify --vp9 drops image-based subtitles that WebM cannot carry."""
    # Given: reference.json (PGS subtitles) with --vp9 and all subtitles kept
    plan_settings.update({"vp9": True, "keep_all_subtitles": True})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: no subtitle stream survives into the WebM plan, and the earlier
    # "Drop unwanted subtitles" action is corrected to reflect that a drop happened
    assert all(s.codec_type != CodecTypes.SUBTITLE for s in plan.streams)
    drop_subs = next(a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL)
    assert drop_subs.applied is True
    assert drop_subs.reason is None


@pytest.mark.parametrize(
    ("settings", "expected_kept"),
    [
        pytest.param({}, True, id="kept in the source container"),
        pytest.param({"vp9": True}, False, id="dropped for WebM"),
    ],
)
def test_build_plan_keeps_attachments_unless_webm(
    make_video, plan_settings, settings, expected_kept
):
    """Verify attachments such as subtitle fonts are kept, except in WebM output."""
    # Given: reference.json with a font attachment
    plan_settings.update(settings)
    video = make_video("reference.json")
    video.probe_box.streams.append(
        Box({"index": 10, "codec_type": CodecTypes.ATTACHMENT, "codec_name": "ttf"})
    )

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: attachments are copied unless the output is WebM, which says so in a notice
    assert plan.keep_attachments is expected_kept
    assert any("attachment stream 0:10" in notice for notice in plan.notices) is not expected_kept


def test_build_plan_vp9_downmix_uses_vorbis(make_video, plan_settings):
    """Verify a downmix track targets libvorbis, not AAC, when --vp9 is active."""
    # Given: a surround-only file with --downmix and --vp9
    plan_settings.update({"vp9": True, "downmix_stereo": True})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

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


def test_clean_no_video_streams_raises(make_video, plan_settings):
    """Verify plan_clean() rejects a file with no video streams before doing any work."""
    # Given: audio_only.json (one audio stream, zero video streams)
    video = make_video("audio_only.json")

    # When: cleaning
    # Then: VideoCleanError is raised, distinct from cappa.Exit, so a batch loop can
    # catch it and continue to the next file without swallowing a real interrupt
    with pytest.raises(VideoCleanError) as exc_info:
        video.plan_clean(*plan_settings.args())
    assert exc_info.value.path == video.path
    assert exc_info.value.reason == NO_VIDEO_STREAMS_REASON


def test_clean_no_audio_streams_raises(make_video, plan_settings):
    """Verify plan_clean() rejects a file with no audio streams before doing any work."""
    # Given: video_only.json (one video stream, zero audio streams)
    video = make_video("video_only.json")

    # When: cleaning
    # Then: VideoCleanError is raised, distinct from cappa.Exit, so a batch loop can
    # catch it and continue to the next file without swallowing a real interrupt
    with pytest.raises(VideoCleanError) as exc_info:
        video.plan_clean(*plan_settings.args())
    assert exc_info.value.path == video.path
    assert exc_info.value.reason == NO_AUDIO_STREAMS_REASON


def test_clean_noop_skips_ffmpeg(make_video, mock_ffmpeg, plan_settings):
    """Verify a plan that changes nothing skips ffmpeg and reports no work."""
    # Given: a file whose only audio has an unmapped channel count and --downmix
    plan_settings.update({"downmix_stereo": True})
    video = make_video("unmapped_channels.json")

    # When: cleaning
    config, options = plan_settings.args()
    result = clean_video_file(video, config=config, options=options)

    # Then: ffmpeg never runs and no action was applied
    mock_ffmpeg.assert_not_called()
    assert all(not action.applied for action in result.actions)


def test_drop_audio_records_applied_when_streams_dropped(make_video, plan_settings):
    """Verify drop-audio is applied when language filtering drops audio streams."""
    # Given: reference.json (french and commentary audio dropped, english kept)
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-audio action is applied with no skip reason
    drop = next(a for a in plan.actions if a.label == DROP_AUDIO_LABEL)
    assert drop.applied is True
    assert drop.reason is None


def test_drop_audio_records_reason_when_nothing_dropped(make_video, plan_settings):
    """Verify drop-audio records a reason when every audio stream matches the keep languages."""
    # Given: audio_only.json (single english audio stream, langs_to_keep=["en"])
    video = make_video("audio_only.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-audio action is skipped with a matching-languages reason
    drop = next(a for a in plan.actions if a.label == DROP_AUDIO_LABEL)
    assert drop.applied is False
    assert drop.reason == ALL_AUDIO_KEPT_REASON


def test_drop_audio_records_fallback_reason_when_every_stream_filtered(make_video, plan_settings):
    """Verify drop-audio records the fallback reason when language filtering drops everything."""
    # Given: audio_only.json (single english audio stream) with langs_to_keep=["fr"] and
    # drop_original_audio=True so nothing matches and the safety fallback keeps it anyway
    plan_settings.update({"langs_to_keep": ["fr"], "drop_original_audio": True})
    video = make_video("audio_only.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-audio action is truthfully unapplied with the fallback reason
    drop = next(a for a in plan.actions if a.label == DROP_AUDIO_LABEL)
    assert drop.applied is False
    assert drop.reason == SILENT_FILE_REASON


def test_downmix_records_applied_when_planned(make_video, plan_settings):
    """Verify downmix is applied when a surround source is downmixed to stereo."""
    # Given: no_stereo.json (no existing stereo mix, 5.1 surround source) with --downmix
    plan_settings.update({"downmix_stereo": True})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the downmix action is applied with no skip reason
    downmix = next(a for a in plan.actions if a.label == DOWNMIX_STEREO_LABEL)
    assert downmix.applied is True
    assert downmix.reason is None


def test_downmix_records_skip_when_stereo_exists(make_video, plan_settings):
    """Verify downmix records a skip reason pointing to --force when stereo already exists."""
    # Given: reference.json (existing non-commentary stereo track) with --downmix, no --force
    plan_settings.update({"downmix_stereo": True, "force": False})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the downmix action is skipped with a --force reason
    downmix = next(a for a in plan.actions if a.label == DOWNMIX_STEREO_LABEL)
    assert downmix.applied is False
    assert "use --force" in downmix.reason


def test_downmix_records_skip_when_no_surround_source_after_force(make_video, plan_settings):
    """Verify a forced downmix with no surround source records a no-surround reason."""
    # Given: stereo_no_surround.json (existing stereo, no surround bed) with --downmix --force
    plan_settings.update({"downmix_stereo": True, "force": True})
    video = make_video("stereo_no_surround.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the downmix action is skipped with a no-surround-source reason
    downmix = next(a for a in plan.actions if a.label == DOWNMIX_STEREO_LABEL)
    assert downmix.applied is False
    assert downmix.reason == NO_SURROUND_SOURCE_REASON


def test_downmix_absent_when_flag_not_set(make_video, plan_settings):
    """Verify no downmix action is recorded when --downmix is not requested."""
    # Given: reference.json without --downmix
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: no downmix action appears in the plan
    assert all(a.label != DOWNMIX_STEREO_LABEL for a in plan.actions)


def test_actions_order_drop_audio_before_downmix(make_video, plan_settings):
    """Verify the drop-audio action is recorded before the downmix action."""
    # Given: no_stereo.json with --downmix
    plan_settings.update({"downmix_stereo": True})
    video = make_video("no_stereo.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: drop-audio precedes downmix in the recorded actions
    labels = [a.label for a in plan.actions]
    assert labels.index(DROP_AUDIO_LABEL) < labels.index(DOWNMIX_STEREO_LABEL)


def test_drop_subtitles_records_applied_when_dropped(make_video, plan_settings):
    """Verify drop-subtitles is applied when default settings drop every local subtitle."""
    # Given: reference.json (all subtitles dropped under default settings)
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-subtitles action is applied with no skip reason
    drop = next(a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL)
    assert drop.applied is True
    assert drop.reason is None


def test_drop_subtitles_records_applied_via_early_drop_all_path(make_video, plan_settings):
    """Verify drop-subtitles is applied when settings drop local subs before evaluation."""
    # Given: reference.json with --drop-local-subs (skips per-stream evaluation entirely)
    plan_settings.update({"drop_local_subs": True})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-subtitles action is still recorded exactly once and applied
    drop_actions = [a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL]
    assert len(drop_actions) == 1
    assert drop_actions[0].applied is True


def test_drop_subtitles_records_reason_when_no_subtitles(make_video, plan_settings):
    """Verify drop-subtitles records a reason when the file has no subtitle streams."""
    # Given: audio_only.json (no subtitle streams)
    video = make_video("audio_only.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-subtitles action is skipped with a no-subtitles reason
    drop = next(a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL)
    assert drop.applied is False
    assert drop.reason == NO_SUBTITLES_REASON


def test_drop_subtitles_records_reason_when_keep_all_subtitles(make_video, plan_settings):
    """Verify drop-subtitles records a --keep-all-subtitles reason when nothing is dropped."""
    # Given: reference.json with --keep-all-subtitles (every subtitle stream kept)
    plan_settings.update({"keep_all_subtitles": True})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-subtitles action is skipped with a --keep-all-subtitles reason
    drop = next(a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL)
    assert drop.applied is False
    assert drop.reason == KEEP_ALL_SUBTITLES_REASON


def test_drop_subtitles_records_reason_when_no_unwanted(make_video, plan_settings):
    """Verify drop-subtitles records a generic reason when every subtitle matches local prefs."""
    # Given: uhd.json (single english subtitle) with --keep-local-subtitles
    plan_settings.update({"keep_local_subtitles": True})
    video = make_video("uhd.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the drop-subtitles action is skipped with a no-unwanted-subtitles reason
    drop = next(a for a in plan.actions if a.label == DROP_SUBTITLES_LABEL)
    assert drop.applied is False
    assert drop.reason == NO_UNWANTED_SUBTITLES_REASON


def test_downmix_picks_one_source_per_file(make_video, plan_settings):
    """Verify a forced downmix builds exactly one stereo track when several 5.1 beds qualify."""
    # Given: reference.json keeps two 5.1 beds (English #2, French #3) with --downmix --force
    plan_settings.update({"downmix_stereo": True, "force": True, "langs_to_keep": ["en", "fr"]})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: only one downmix stream is appended
    downmixes = [s for s in plan.streams if s.stream_filter is not None]
    assert len(downmixes) == 1


def test_downmix_prefers_first_configured_language(make_video, plan_settings):
    """Verify the downmix source follows the order of the configured keep languages."""
    # Given: reference.json keeps two 5.1 beds (English #2, French #3) with French listed first
    plan_settings.update({"downmix_stereo": True, "force": True, "langs_to_keep": ["fr", "en"]})
    video = make_video("reference.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the French bed alone seeds the downmix even though the English bed comes first
    downmixes = [s for s in plan.streams if s.stream_filter is not None]
    assert [s.source_index for s in downmixes] == [3]


def test_downmix_discounts_commentary_source(make_video, plan_settings):
    """Verify a surround commentary track is not chosen as the downmix source."""
    # Given: a kept, untagged-language 5.1 commentary bed (#1) ahead of the main 5.1 bed (#2)
    plan_settings.update({"downmix_stereo": True, "keep_commentary": True})
    video = make_video("surround_commentary_no_lang.json")

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the main bed alone seeds the downmix, not the commentary bed listed first
    downmixes = [s for s in plan.streams if s.stream_filter is not None]
    assert [s.source_index for s in downmixes] == [2]


def test_audio_plan_drops_untagged_commentary(make_video, plan_settings):
    """Verify an untagged commentary track is dropped unless commentary is kept."""
    # Given: an untagged-language 5.1 commentary bed (#1) and the main 5.1 bed (#2)
    video = make_video("surround_commentary_no_lang.json")

    # When: building the plan with the default keep_commentary=false
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the commentary bed is not mapped, but the main bed is
    audio = [s.source_index for s in plan.streams if s.codec_type == CodecTypes.AUDIO]
    assert audio == [2]


@pytest.mark.parametrize(
    ("keep_commentary", "expected_subtitles"),
    [
        pytest.param(False, [], id="commentary dropped by default"),
        pytest.param(True, [6], id="commentary kept when asked"),
    ],
)
def test_subtitle_plan_drops_commentary_unless_kept(
    make_video, plan_settings, keep_commentary, expected_subtitles
):
    """Verify a commentary subtitle in a kept language is dropped unless commentary is kept."""
    # Given: reference.json whose English subtitle (#6) is a commentary track, with local
    # subtitles kept
    plan_settings.update({"keep_local_subtitles": True, "keep_commentary": keep_commentary})
    video = make_video("reference.json")
    video.probe_box.streams[6].title = "Commentary"

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the commentary subtitle is mapped only when commentary is kept
    subtitles = [s.source_index for s in plan.streams if s.codec_type == CodecTypes.SUBTITLE]
    assert subtitles == expected_subtitles


@pytest.mark.parametrize(
    ("stream_index", "tag", "settings", "expected_kept"),
    [
        pytest.param(3, "qaa", {}, True, id="audio reserved code kept as unknown"),
        pytest.param(3, "iw", {}, True, id="audio deprecated code kept as unknown"),
        pytest.param(3, "FRA", {}, False, id="audio uppercase code matched case-insensitively"),
        pytest.param(
            7, "DAN", {"langs_to_keep": ["da"], "keep_local_subtitles": True}, True, id="subtitle"
        ),
        pytest.param(7, "qaa", {"keep_local_subtitles": True}, False, id="subtitle unusable"),
    ],
)
def test_plan_tolerates_unusable_language_tags(
    make_video, plan_settings, stream_index, tag, settings, expected_kept
):
    """Verify a language tag iso639 rejects is treated as unknown instead of aborting the plan."""
    # Given: reference.json with one stream's language tag replaced
    plan_settings.update(settings)
    video = make_video("reference.json")
    next(s for s in video.probe_box.streams if s.index == stream_index).language = tag

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the stream is kept or dropped by its parsed language
    assert (stream_index in {s.source_index for s in plan.streams}) is expected_kept


@pytest.mark.parametrize(
    ("stream_index", "tag", "expected_kept"),
    [
        pytest.param(3, None, True, id="untagged audio kept"),
        pytest.param(3, "und", True, id="und audio kept"),
        pytest.param(7, None, False, id="untagged subtitle dropped"),
        pytest.param(7, "und", True, id="und subtitle kept"),
    ],
)
def test_plan_handles_streams_without_a_language(
    make_video, plan_settings, stream_index, tag, expected_kept
):
    """Verify untagged audio and `und` streams are kept, while untagged subtitles are dropped."""
    # Given: reference.json with one non-English stream's language tag removed or set to
    # `und`, keeping local subtitles
    plan_settings.update({"keep_local_subtitles": True})
    video = make_video("reference.json")
    next(s for s in video.probe_box.streams if s.index == stream_index).language = tag

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the stream is kept or dropped by the rule for its type
    assert (stream_index in {s.source_index for s in plan.streams}) is expected_kept


def test_forced_downmix_keeps_stereo_in_other_languages(make_video, plan_settings):
    """Verify a forced downmix drops only the stereo track in the surround source's language."""
    # Given: reference.json with the English stereo (#4) retagged French, keeping both languages
    plan_settings.update({"downmix_stereo": True, "force": True, "langs_to_keep": ["en", "fr"]})
    video = make_video("reference.json")
    next(s for s in video.probe_box.streams if s.index == 4).language = "fra"

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: the French stereo survives next to the stereo rebuilt from the English bed
    mapped = [s.source_index for s in plan.streams]
    assert 4 in mapped
    downmixes = [s.source_index for s in plan.streams if s.stream_filter is not None]
    assert downmixes == [2]


@pytest.mark.parametrize(
    ("width", "force", "expected_upscale_width", "expected_applied"),
    [
        pytest.param(1280, True, 1280, True, id="forced narrower source is an upscale"),
        pytest.param(1280, False, None, False, id="unforced narrower source is skipped"),
        pytest.param(1920, True, None, True, id="forced 1080p source is not an upscale"),
        pytest.param(3840, False, None, True, id="4K source is a downscale"),
    ],
)
def test_scale_records_upscale_source_width(
    make_video, plan_settings, width, force, expected_upscale_width, expected_applied
):
    """Verify the plan names the source width only when --1080p would upscale it."""
    # Given: reference.json with the video stream resized
    plan_settings.update({"video_1080": True, "force": force})
    video = make_video("reference.json")
    next(s for s in video.probe_box.streams if s.codec_type == CodecTypes.VIDEO).width = width

    # When: building the plan
    plan = video._build_plan(*plan_settings.args())  # noqa: SLF001

    # Then: only a forced narrower source records an upscale
    assert plan.upscale_source_width == expected_upscale_width
    scale_action = next(a for a in plan.actions if a.label == SCALE_TO_1080P_LABEL)
    assert scale_action.applied is expected_applied
