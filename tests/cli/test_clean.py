"""Test the vidcleaner clean subcommand."""

import cappa
import pytest
from cappa.base import invoke
from iso639 import Lang

from vid_cleaner.constants import (
    ALREADY_MODERN_CODEC_REASON,
    CONVERT_TO_H265_LABEL,
    DOWNMIX_STEREO_LABEL,
    DROP_AUDIO_LABEL,
    DROP_SUBTITLES_LABEL,
    KEEP_ALL_SUBTITLES_REASON,
    REORDER_STREAMS_LABEL,
    STEREO_EXISTS_REASON,
    STREAMS_IN_ORDER_REASON,
    TREE_LAST,
    UPSCALE_DECLINED_REASON,
)
from vid_cleaner.vidcleaner import VidCleaner, configure_output

from vid_cleaner.models.video_file import VideoFile  # isort: skip
from vid_cleaner.controllers import TempFile  # isort: skip


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            [f"✔ {DROP_AUDIO_LABEL}", f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Defaults (only keep local audio,no commentary)",
        ),
        pytest.param(
            ["--downmix"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            [f"✖ {DOWNMIX_STEREO_LABEL}  ({STEREO_EXISTS_REASON})"],
            id="Don't convert audio to stereo when stereo exists",
        ),
        pytest.param(
            ["--keep-commentary"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:5"],
            [f"✔ {DROP_AUDIO_LABEL}"],
            id="Keep commentary",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            [f"✔ {DROP_AUDIO_LABEL}"],
            id="Keep local language from config even when dropped",
        ),
        pytest.param(
            ["--langs", "fr,es"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:8"],
            [f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Keep specified languages",
        ),
        pytest.param(
            ["--keep-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6 -map 0:7 -map 0:8"],
            [f"✖ {DROP_SUBTITLES_LABEL}  ({KEEP_ALL_SUBTITLES_REASON})"],
            id="Keep all subtitles",
        ),
        pytest.param(
            ["--keep-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            [f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Keep local subtitles",
        ),
    ],
)
def test_stream_processing(
    debug,
    mocker,
    mock_ffprobe_box,
    mock_ffmpeg,
    capsys,
    mock_video_path,
    args,
    command_expected,
    process_output,
) -> None:
    """Verify clean command processes video streams according to specified options."""
    # Given: Mock video file and processing options
    args = ["clean", "-vv", *args, str(mock_video_path)]

    # And: Mocked external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Running clean command
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    # debug(output, "output")

    # Then: FFmpeg is called with correct stream mapping
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    for fragment in command_expected:
        assert fragment in command

    # And: Success messages are displayed. The fixture's video stream is already first, so
    # under -vv the reorder action shows skipped rather than absent.
    assert exc_info.value.code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    for fragment in process_output:
        assert fragment in output
    assert "cleaned_video.mkv" in output


def test_clean_video_foreign_language_keeps_und_subtitle(
    mocker,
    mock_video_path,
    capsys,
    tmp_path,
    mock_ffprobe_box,
    mock_ffmpeg,
    debug,
):
    """Verify a subtitle tagged "und" is kept when the original audio language differs.

    Regression test for a bug where `stream.language.lower` was compared to `"und"`
    without calling it, so the comparison was always False and the "und" subtitle was
    silently dropped instead of kept.
    """
    # Given: a video whose original audio language (fr) is not in langs_to_keep (en),
    # forcing the "keep subtitles when original audio differs" branch to decide, and a
    # subtitle stream tagged "und" that branch must keep
    args = ["clean", "-vv", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("und_subtitle.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("fr"))

    # When: processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: the "und" subtitle (index 7) is kept alongside the English subtitle (index 6),
    # while the French subtitle (index 8) is dropped
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert exc_info.value.code == 0
    assert "-map 0:6" in command
    assert "-map 0:7" in command
    assert "-map 0:8" not in command
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:6"],
            [f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Defaults keep local and original audio, local subs",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            [f"✔ {DROP_AUDIO_LABEL}", f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Drop original audio (keeps local audio)",
        ),
        pytest.param(
            ["--drop-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4"],
            [f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Drop local subs",
        ),
    ],
)
def test_clean_video_foreign_language(
    mocker,
    mock_video_path,
    capsys,
    tmp_path,
    mock_ffprobe_box,
    mock_ffmpeg,
    debug,
    args,
    command_expected,
    process_output,
):
    """Verify that video cleaning correctly processes foreign language videos."""
    args = ["clean", "-vv", *args, str(mock_video_path)]

    # And: Mock external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("fr"))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    # debug(output, "output")

    # THEN verify the ffmpeg command contains expected stream mappings
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])

    # AND verify the command output indicates successful processing. The fixture's video
    # stream is already first, so under -vv the reorder action shows skipped rather than absent.
    assert exc_info.value.code == 0
    for fragment in command_expected:
        assert fragment in command
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    for fragment in process_output:
        assert fragment in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2"],
            f"✔ {DROP_AUDIO_LABEL}",
            id="Defaults, drops commentary",
        ),
        pytest.param(
            ["--downmix"],
            [
                "-map 0:0 -map 0:1 -map 0:2 -map 0:2",
                "-c:a:0 copy",
                "-c:a:1 copy",
                "-c:a:2 aac -filter:a:2 pan=stereo",
                "-ac:a:2 2 -b:a:2 256k -ar:a:2 48000",
                "-metadata:s:a:2 title=2.0",
            ],
            "✔ Downmix to stereo",
            id="Defaults",
        ),
    ],
)
def test_clean_video_downmix(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    tmp_path,
    mock_ffmpeg,
    debug,
    args,
    command_expected,
    process_output,
):
    """Verify that videos without stereo audio are correctly downmixed."""
    args = ["clean", "-vv", *args, str(mock_video_path)]

    # And: Mock external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    # debug(output, "output")

    # Then: FFmpeg is called with correct stream mapping
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    for fragment in command_expected:
        assert fragment in command

    # And: Success messages are displayed. The fixture's video stream is already first, so
    # under -vv the reorder action shows skipped rather than absent.
    assert exc_info.value.code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert process_output in output
    assert "cleaned_video.mkv" in output


def test_clean_video_downmix_stereo_commentary_kept(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify a kept stereo commentary track does not block downmixing the main track."""
    # Given: A file with a surround main track and a no-language stereo commentary track
    args = ["clean", "-vv", "--downmix", "--keep-commentary", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("commentary_no_lang.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with downmix requested
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: The main surround track is downmixed despite the stereo commentary track.
    # Two audio streams are kept, so the downmix is output audio stream index 2.
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert "-map 0:1 -map 0:2 -map 0:1" in command
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2" in command

    # And: The commentary track is kept and the file is processed
    assert exc_info.value.code == 0
    assert "-map 0:2" in command
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_unmapped_channel_count(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify downmix skips audio with an unmapped channel count instead of crashing."""
    # Given: A file whose only audio track has an unmapped channel count (3ch -> None)
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("unmapped_channels.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with downmix requested
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: It completes successfully without raising an unexpected error. Under -vv every
    # skipped operation is shown (none applied), not the normal-mode "No changes needed" line.
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "✖ Downmix to stereo  (no surround source to downmix)" in output


def test_clean_video_downmix_dialogue_forward_filter(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify the downmix uses the dialogue-forward filter with LFE dropped."""
    # Given: A file with surround audio and no stereo track
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with downmix requested
    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The dialogue-forward chain is applied, the center is dominant, and LFE is dropped
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert "pan=stereo|FL=0.7*FC" in command
    assert "acompressor=" in command
    assert "loudnorm=" in command
    assert "LFE" not in command


def test_clean_trace_prints_stream_specifiers_verbatim(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify -vv prints ffmpeg stream specifiers such as :a: as text, not emoji."""
    # Given: A downmix, whose ffmpeg command carries per-stream audio options
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with trace output
    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The traced command shows the audio stream specifier as typed
    output = capsys.readouterr().out
    assert "-c:a:" in output
    assert "🅰" not in output


def test_clean_video_downmix_atmos(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify a channel-based Atmos track above 7.1 is downmixed to stereo."""
    # Given: A file whose only audio is a 12-channel 7.1.4 Atmos track with no stereo
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("atmos_no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with downmix requested
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The Atmos track is downmixed rather than passed through un-downmixed.
    # One audio stream is kept, so the downmix is output audio stream index 1.
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    output = capsys.readouterr().out
    assert "-map 0:1 -map 0:1" in command
    assert "-c:a:1 aac" in command
    assert "-ac:a:1 2" in command
    assert "pan=stereo|FL=0.7*FC" in command
    assert exc_info.value.code == 0
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_does_not_clobber_kept_stream(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify the downmix targets its own output stream, not a kept surround track."""
    # Given: A file with two surround tracks (7.1 + 5.1) and no stereo
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file with downmix requested
    with pytest.raises(cappa.Exit):
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: The two kept streams are output audio 0 and 1, and the downmix codec/filter
    # options bind to index 2, so no encode option lands on the copied surround tracks.
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert "-c:a:0 copy" in command
    assert "-c:a:1 copy" in command
    assert "-filter:a:2" in command
    assert "-filter:a:0" not in command
    assert "-filter:a:1" not in command


def test_clean_reorganize_streams(
    mocker, mock_ffprobe_box, mock_video_path, tmp_path, capsys, mock_ffmpeg, debug
):
    """Verify streams in the wrong order are remapped video-first in the single pass."""
    # Given: a file whose video stream is not first
    args = ["clean", "-vv", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("wrong_order.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: one ffmpeg pass maps the streams in video-first order
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert exc_info.value.code == 0
    assert "-map 0:2 -map 0:1 -map 0:3" in command
    assert f"✔ {REORDER_STREAMS_LABEL}" in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "conversion_output"),
    [
        pytest.param(
            ["--h265"],
            [
                "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
                "-c:v:0 libx265",
                "-b:v:0 0k -minrate:v:0 0k -maxrate:v:0 0k -bufsize:v:0 0k",
                "-c:a:0 copy",
                "-c:a:1 copy",
                "-c:a:2 copy",
            ],
            "✔ Convert to H.265",
            id="Convert to h265",
        ),
        pytest.param(
            ["--vp9"],
            [
                "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
                "-c:v:0 libvpx-vp9",
                "-b:v:0 0 -crf:v:0 30",
                "-c:a:0 libvorbis",
                "-dn -map_chapters -1",
            ],
            "✔ Convert to VP9",
            id="Convert to vp9",
        ),
    ],
)
def test_convert_video(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    tmp_path,
    mock_ffmpeg,
    capsys,
    debug,
    args,
    command_expected,
    conversion_output,
):
    """Verify codec conversion happens in the same single ffmpeg pass as stream selection."""
    # Given: reference.json probe data and a conversion flag
    args = ["clean", "-vv", *args, str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(TempFile, "new_tmp_path", return_value=(mock_video_path))
    mocker.patch.object(TempFile, "latest_temp_path", return_value=(mock_video_path))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: ffmpeg runs exactly once with selection and conversion combined
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    for fragment in command_expected:
        assert fragment in command

    # And: the result tree itemizes the conversion, proving the codec change ran alongside
    # stream selection in the same pass
    assert exc_info.value.code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert conversion_output in output
    if "--vp9" in args:
        assert "Converting to VP9, setting output to `test_video.webm`" in output


def test_clean_video_h265_skip_shown_in_debug_mode(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
):
    """Verify -vv reports a skipped --h265 request with its reason when already H.265."""
    # Given: reference.json rewritten so the video stream is already HEVC
    args = ["clean", "-vv", "--h265", str(mock_video_path)]
    box = mock_ffprobe_box("reference.json")
    box.streams[0].codec_name = "hevc"
    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", return_value=box)
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: running clean in debug mode with --h265 requested
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: the skipped H.265 conversion is reported with its reason
    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert f"✖ {CONVERT_TO_H265_LABEL}  ({ALREADY_MODERN_CODEC_REASON})" in output


def test_clean_video_h265_skip_hidden_in_normal_mode(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
):
    """Verify normal mode (no -vv) hides the skipped --h265 request and its reason."""
    # Given: reference.json rewritten so the video stream is already HEVC, and no -vv flag
    args = ["clean", "--h265", str(mock_video_path)]
    box = mock_ffprobe_box("reference.json")
    box.streams[0].codec_name = "hevc"
    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", return_value=box)
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: running clean in normal mode with --h265 requested
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: normal mode is genuinely active and the skipped operation is not shown
    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "✖ Convert to H.265" not in output
    assert "already H.265/VP9; use --force" not in output
    # But verify that rendering actually happened (positive assertion)
    assert f"✔ {DROP_AUDIO_LABEL}" in output


def test_clean_renders_completed_steps_on_error(
    mocker, mock_ffprobe_box, mock_ffmpeg, capsys, mock_video_path
) -> None:
    """Verify operations render up front even when the later write step raises.

    A write failure is a per-file failure like any other: it is reported and the batch
    exits non-zero rather than propagating raw, since a single write error must not be
    able to look different from any other file-level failure to the caller.
    """
    # Given: operations render up front inside clean(), before the write step, so a
    # failing write can't hide what already completed
    args = ["clean", "-vv", "--h265", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=RuntimeError("boom"),
    )

    # When: running clean and the output write raises after processing completes
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    # Then: the operations render up front, before the failing write, proving the CLI
    # reports what actually happened even when `write_output()` raises. reference.json's
    # video stream is already first, so reorder is skipped and H.265 conversion is the
    # operation that proves clean() ran to completion. The write failure is reported and
    # the command exits non-zero rather than crashing.
    output, error = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "✔ Convert to H.265" in output
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "1 file(s) failed" in output + error


def test_clean_video_downmix_skip_when_stereo_exists(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify --downmix without --force skips and notifies when a stereo track exists."""
    # Given: reference.json keeps a 7.1, a 5.1, and a non-commentary stereo track
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing with downmix requested but not forced
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])

    # Then: The existing stereo track is kept and no downmix filter is added
    assert exc_info.value.code == 0
    assert "-map 0:4" in command
    assert "-filter:a:" not in command
    assert f"✖ {DOWNMIX_STEREO_LABEL}  ({STEREO_EXISTS_REASON})" in output


def test_clean_video_downmix_force_recreates_stereo(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify --downmix --force drops the existing stereo track and rebuilds from surround."""
    # Given: reference.json with a non-commentary stereo track at index 4
    args = ["clean", "-vv", "--downmix", "--force", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Processing with downmix forced
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])

    # Then: The old stereo (index 4) is dropped and a fresh downmix is built from the 5.1
    # (index 2). Two audio streams remain mapped, so the downmix is output audio index 2.
    assert exc_info.value.code == 0
    assert "-map 0:0 -map 0:1 -map 0:2 -map 0:2" in command
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2 -b:a:2 256k -ar:a:2 48000" in command
    assert "-map 0:4" not in command
    # reference.json's English subtitle matches langs_to_keep and the original language,
    # so it is dropped rather than kept, and the title carries no subtitle flag
    assert "-map 0:6" not in command
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_force_recreates_from_multiple_stereo(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify --downmix --force drops every existing stereo track and rebuilds one from surround."""
    # Given: two non-commentary stereo tracks plus a 5.1 surround bed
    args = ["clean", "-vv", "--downmix", "--force", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("two_stereo_and_surround.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Forcing downmix with two stereo tracks present
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])

    # Then: Both stereo tracks (index 2 and 3) are dropped and one downmix is rebuilt from the
    # 5.1 (index 1). Only the 5.1 remains mapped, so the downmix is output audio index 1.
    assert exc_info.value.code == 0
    assert "-map 0:2" not in command
    assert "-map 0:3" not in command
    assert "-map 0:1 -map 0:1" in command
    assert "-c:a:1 aac" in command
    assert "-ac:a:1 2 -b:a:1 256k" in command
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_force_no_surround_keeps_stereo(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
):
    """Verify --downmix --force keeps the existing stereo and notifies when no surround exists."""
    # Given: A file whose only audio track is a non-commentary stereo track
    args = ["clean", "-vv", "--downmix", "--force", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("stereo_no_surround.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Forcing downmix with no surround source to rebuild from
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: The stereo track is kept (all streams pass through) and the user is notified.
    # Under -vv every skipped operation is shown (none applied), including the downmix
    # itself, rather than the normal-mode "No changes needed" line.
    assert exc_info.value.code == 0
    assert "✖ Downmix to stereo  (no surround source to downmix)" in output


def test_clean_video_downmix_force_noop_without_existing_stereo(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
    make_config,
):
    """Verify --force does not change the normal downmix path when no stereo track exists."""
    # Given: no_stereo.json (7.1 + 5.1 + a stereo commentary track only)
    args = ["clean", "-vv", "--downmix", "--force", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("no_stereo.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Forcing downmix with no existing stereo mix
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])

    # Then: The downmix is built from the 5.1 exactly as an un-forced --downmix would
    assert exc_info.value.code == 0
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2 -b:a:2 256k" in command
    assert "-filter:a:2" in command
    assert "✔ Downmix to stereo" in output


def test_clean_renders_one_tree_per_file(
    mocker, mock_ffprobe_box, mock_video_path, capsys, mock_ffmpeg
):
    """Verify a file's operations and outcomes close a single tree, not one each."""
    # Given: A file whose operations render up front and whose write then reports a save
    args = ["clean", "-vv", "--h265", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[configure_output])

    output = capsys.readouterr().out

    # Then: Everything printed for the file closes a single tree, on its last line
    assert exc_info.value.code == 0
    per_file = output.rsplit("\u21e8", 1)[-1]
    assert per_file.count(TREE_LAST) == 1
    assert TREE_LAST in per_file.splitlines()[-1]


def test_clean_debug_output_reports_unusable_language_tags(
    mocker, mock_ffprobe_box, mock_video_path, capsys, mock_ffmpeg
):
    """Verify -vv names a language tag that planning treated as unknown."""
    # Given: reference.json with the French 5.1 track tagged with a reserved code
    probe = mock_ffprobe_box("reference.json")
    next(s for s in probe.streams if s.index == 3).language = "qaa"
    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", return_value=probe)
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Dry-running clean with debug output
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(
            obj=VidCleaner,
            argv=["-n", "-vv", "clean", str(mock_video_path)],
            deps=[configure_output],
        )

    # Then: The debug output names the stream and its unusable tag
    assert exc_info.value.code == 0
    assert "Stream #3: unusable language tag 'qaa'" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("is_terminal", "answer", "dryrun", "expect_scaled", "expect_prompt"),
    [
        pytest.param(True, True, False, True, True, id="approved upscale runs"),
        pytest.param(True, False, False, False, True, id="declined upscale is skipped"),
        pytest.param(True, EOFError(), False, False, True, id="closed stdin skips the upscale"),
        pytest.param(False, None, False, False, False, id="non-interactive skips the upscale"),
        pytest.param(True, None, True, True, False, id="dry run warns without asking"),
    ],
)
def test_clean_asks_before_upscaling_to_1080p(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    interactive_console,
    is_terminal,
    answer,
    dryrun,
    expect_scaled,
    expect_prompt,
):
    """Verify --1080p --force on a smaller video warns and upscales only when approved."""
    # Given: a 1280px-wide video and a user who answers the upscale prompt
    probe = mock_ffprobe_box("reference.json")
    next(s for s in probe.streams if s.codec_type == "video").width = 1280
    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", return_value=probe)
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved"]),
    )
    interactive_console(is_terminal=is_terminal)
    ask = mocker.patch(
        "vid_cleaner.cli.clean_video.Confirm.ask",
        **({"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}),
    )
    args = [*(["-n"] if dryrun else []), "-vv", "clean", "--1080p", "--force"]

    # When: cleaning the file
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=[*args, str(mock_video_path)], deps=[configure_output])

    # Then: the user was warned, and the video is scaled only when the upscale went ahead
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exc_info.value.code == 0
    assert "1280px wide" in output
    assert ask.called is expect_prompt
    if dryrun:
        mock_ffmpeg.assert_not_called()
        assert "scale=width=1920" in output
    else:
        command = " ".join(mock_ffmpeg.call_args[0][0])
        assert ("scale=width=1920" in command) is expect_scaled
    if not expect_scaled:
        assert UPSCALE_DECLINED_REASON in output
