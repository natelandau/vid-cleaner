# type: ignore
"""Test the ConversionPlan model."""

from vid_cleaner.constants import CodecTypes
from vid_cleaner.models import ConversionPlan, OutputStream
from vid_cleaner.models.conversion_plan import PlanAction


def test_build_command_maps_streams_in_order():
    """Verify build_command emits one -map per stream in plan order."""
    # Given: a plan with video, audio, and subtitle streams out of input order
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=2, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=0, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=1, codec_type=CodecTypes.SUBTITLE),
        ]
    )

    # When: building the command
    command = " ".join(plan.build_command())

    # Then: maps come first, in plan order, with per-stream copy codecs
    assert command.startswith("-map 0:2 -map 0:0 -map 0:1")
    assert "-c:v:0 copy" in command
    assert "-c:a:0 copy" in command
    assert "-c:s:0 copy" in command


def test_build_command_indexes_options_per_output_type():
    """Verify per-stream options use type-scoped output indices, not input indices."""
    # Given: two copied audio streams and a downmix-style encoded third
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=0, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=3, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=5, codec_type=CodecTypes.AUDIO),
            OutputStream(
                source_index=3,
                codec_type=CodecTypes.AUDIO,
                codec="aac",
                stream_filter="pan=stereo|FL=FC",
                extra_args=["-ac:a:{n}", "2", "-b:a:{n}", "256k"],
                metadata={"title": "2.0"},
            ),
        ]
    )

    # When: building the command
    command = " ".join(plan.build_command())

    # Then: the encoded stream binds to output audio index 2, and the source is mapped twice
    assert "-map 0:0 -map 0:3 -map 0:5 -map 0:3" in command
    assert "-c:a:2 aac -filter:a:2 pan=stereo|FL=FC -ac:a:2 2 -b:a:2 256k" in command
    assert "-metadata:s:a:2 title=2.0" in command
    assert "-c:a:0 copy" in command
    assert "-c:a:1 copy" in command
    assert "-filter:a:0" not in command


def test_build_command_omits_codec_when_none():
    """Verify a None codec emits no -c option so ffmpeg picks the container default."""
    # Given: a video stream being scaled with no explicit codec
    plan = ConversionPlan(
        streams=[
            OutputStream(
                source_index=0,
                codec_type=CodecTypes.VIDEO,
                codec=None,
                stream_filter="scale=width=1920:height=-2",
            ),
        ]
    )

    # When: building the command
    command = " ".join(plan.build_command())

    # Then: the filter is present but no codec option is emitted
    assert "-filter:v:0 scale=width=1920:height=-2" in command
    assert "-c:v:0" not in command


def test_build_command_appends_global_args():
    """Verify global args land at the end of the command."""
    # Given: a plan with global args
    plan = ConversionPlan(
        streams=[OutputStream(source_index=0, codec_type=CodecTypes.VIDEO)],
        global_args=["-dn", "-map_chapters", "-1"],
    )

    # When: building the command
    command = plan.build_command()

    # Then: the global args are the final elements
    assert command[-3:] == ["-dn", "-map_chapters", "-1"]


def test_is_noop_true_for_full_passthrough():
    """Verify a plan copying every input stream in order is a no-op."""
    # Given: three streams mapped in original order, all copy
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=0, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=1, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=2, codec_type=CodecTypes.SUBTITLE),
        ]
    )

    # When/Then: the plan is a no-op for a 3-stream input
    assert plan.is_noop(stream_count=3) is True


def test_is_noop_false_when_stream_dropped():
    """Verify dropping a stream makes the plan not a no-op."""
    # Given: only two of three input streams mapped
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=0, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=1, codec_type=CodecTypes.AUDIO),
        ]
    )

    # When/Then: the plan is not a no-op for a 3-stream input
    assert plan.is_noop(stream_count=3) is False


def test_is_noop_false_when_streams_reordered():
    """Verify a plan that copies every stream but reorders them is not a no-op."""
    # Given: three copy streams whose source_index order is not the identity mapping
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=2, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=1, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=0, codec_type=CodecTypes.SUBTITLE),
        ]
    )

    # When/Then: the plan is not a no-op for a 3-stream input
    assert plan.is_noop(stream_count=3) is False


def test_is_noop_false_when_source_index_duplicated():
    """Verify a plan that duplicates a source index while dropping another is not a no-op."""
    # Given: three copy streams whose source_index list duplicates index 0 and skips index 1
    plan = ConversionPlan(
        streams=[
            OutputStream(source_index=0, codec_type=CodecTypes.VIDEO),
            OutputStream(source_index=0, codec_type=CodecTypes.AUDIO),
            OutputStream(source_index=2, codec_type=CodecTypes.SUBTITLE),
        ]
    )

    # When/Then: the plan is not a no-op for a 3-stream input
    assert plan.is_noop(stream_count=3) is False


def test_is_noop_false_when_any_stream_encodes():
    """Verify an encode, filter, suffix change, or global arg defeats the no-op check."""
    # Given: full passthrough except a video encode
    encoded = ConversionPlan(
        streams=[OutputStream(source_index=0, codec_type=CodecTypes.VIDEO, codec="libx265")]
    )
    filtered = ConversionPlan(
        streams=[
            OutputStream(
                source_index=0, codec_type=CodecTypes.VIDEO, stream_filter="scale=w=1920:h=-2"
            )
        ]
    )
    rewrapped = ConversionPlan(
        streams=[OutputStream(source_index=0, codec_type=CodecTypes.VIDEO)],
        output_suffix=".webm",
    )

    # When/Then: none are no-ops
    assert encoded.is_noop(stream_count=1) is False
    assert filtered.is_noop(stream_count=1) is False
    assert rewrapped.is_noop(stream_count=1) is False


def test_plan_action_defaults():
    """Verify PlanAction defaults reason to None."""
    action = PlanAction(label="Downmix to stereo", applied=True)
    assert action.label == "Downmix to stereo"
    assert action.applied is True
    assert action.reason is None


def test_conversion_plan_actions_default_empty():
    """Verify ConversionPlan actions default to empty list and can be mutated."""
    plan = ConversionPlan()
    assert plan.actions == []
    plan.actions.append(
        PlanAction(label="Reorder streams", applied=False, reason="streams already in order")
    )
    assert plan.actions[0].reason == "streams already in order"
