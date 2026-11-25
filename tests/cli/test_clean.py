"""Test the vidcleaner clean subcommand."""

import pytest
from iso639 import Lang

from tests.helpers import ffmpeg_command, run_vidcleaner
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

from vid_cleaner.controllers import TempFile  # isort: skip


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            [f"✔ {DROP_AUDIO_LABEL}", f"✔ {DROP_SUBTITLES_LABEL}"],
            id="Defaults (only keep local audio, no commentary)",
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
            ["--langs", "fr, es"],
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
    mock_ffmpeg,
    capsys,
    video_path,
    args,
    command_expected,
    process_output,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
) -> None:
    """Verify clean command processes video streams according to specified options."""
    # Given: Mock video file and processing options
    args = ["clean", "-vv", *args, str(video_path)]

    # And: Mocked external dependencies
    mock_video_probe("reference.json")

    # When: Running clean command
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: FFmpeg is called with correct stream mapping
    command = ffmpeg_command(mock_ffmpeg)
    for fragment in command_expected:
        assert fragment in command

    # And: Success messages are displayed. The fixture's video stream is already first, so
    # under -vv the reorder action shows skipped rather than absent.
    assert exit_code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    for fragment in process_output:
        assert fragment in output
    assert "✔ Saved to" in output


def test_clean_video_foreign_language_keeps_und_subtitle(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify a subtitle tagged "und" is kept when the original audio language differs.

    Regression test for a bug where `stream.language.lower` was compared to `"und"`
    without calling it, so the comparison was always False and the "und" subtitle was
    silently dropped instead of kept.
    """
    # Given: a video whose original audio language (fr) is not in langs_to_keep (en),
    # forcing the "keep subtitles when original audio differs" branch to decide, and a
    # subtitle stream tagged "und" that branch must keep
    args = ["clean", "-vv", str(video_path)]
    mock_video_probe("und_subtitle.json")
    mock_original_language.return_value = Lang("fr")

    # When: processing the video file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: the "und" subtitle (index 7) is kept alongside the English subtitle (index 6),
    # while the French subtitle (index 8) is dropped
    command = ffmpeg_command(mock_ffmpeg)
    assert exit_code == 0
    assert "-map 0:6" in command
    assert "-map 0:7" in command
    assert "-map 0:8" not in command
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "✔ Saved to" in output


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
    video_path,
    capsys,
    mock_ffmpeg,
    args,
    command_expected,
    process_output,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify that video cleaning correctly processes foreign language videos."""
    # Given: A French-original video
    args = ["clean", "-vv", *args, str(video_path)]
    mock_video_probe("reference.json")
    mock_original_language.return_value = Lang("fr")

    # When: Processing the video file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The ffmpeg command maps the expected streams
    command = ffmpeg_command(mock_ffmpeg)

    # And: The output reports each step. The fixture's video
    # stream is already first, so under -vv the reorder action shows skipped rather than absent.
    assert exit_code == 0
    for fragment in command_expected:
        assert fragment in command
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    for fragment in process_output:
        assert fragment in output
    assert "✔ Saved to" in output


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
            id="downmix without stereo",
        ),
    ],
)
def test_clean_video_downmix(
    video_path,
    capsys,
    mock_ffmpeg,
    args,
    command_expected,
    process_output,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify that videos without stereo audio are correctly downmixed."""
    args = ["clean", "-vv", *args, str(video_path)]

    # And: Mock external dependencies
    mock_video_probe("no_stereo.json")

    # When: Processing the video file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: FFmpeg is called with correct stream mapping
    command = ffmpeg_command(mock_ffmpeg)
    for fragment in command_expected:
        assert fragment in command

    # And: Success messages are displayed. The fixture's video stream is already first, so
    # under -vv the reorder action shows skipped rather than absent.
    assert exit_code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert process_output in output
    assert "✔ Saved to" in output


def test_clean_video_downmix_stereo_commentary_kept(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify a kept stereo commentary track does not block downmixing the main track."""
    # Given: A file with a surround main track and a no-language stereo commentary track
    args = ["clean", "-vv", "--downmix", "--keep-commentary", str(video_path)]
    mock_video_probe("commentary_no_lang.json")

    # When: Processing the video file with downmix requested
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The main surround track is downmixed despite the stereo commentary track.
    # Two audio streams are kept, so the downmix is output audio stream index 2.
    command = ffmpeg_command(mock_ffmpeg)
    assert "-map 0:1 -map 0:2 -map 0:1" in command
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2" in command

    # And: The file is processed
    assert exit_code == 0
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_unmapped_channel_count(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify downmix skips audio with an unmapped channel count instead of crashing."""
    # Given: A file whose only audio track has an unmapped channel count (3ch -> None)
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("unmapped_channels.json")

    # When: Processing the video file with downmix requested
    exit_code = run_vidcleaner(args)

    # Then: It completes successfully without raising an unexpected error. Under -vv every
    # skipped operation is shown (none applied), not the normal-mode "No changes needed" line.
    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "✖ Downmix to stereo  (no surround source to downmix)" in output


def test_clean_video_downmix_dialogue_forward_filter(
    video_path, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify the downmix uses the dialogue-forward filter with LFE dropped."""
    # Given: A file with surround audio and no stereo track
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("no_stereo.json")

    # When: Processing the video file with downmix requested
    run_vidcleaner(args)

    # Then: The dialogue-forward chain is applied, the center is dominant, and LFE is dropped
    command = ffmpeg_command(mock_ffmpeg)
    assert "pan=stereo|FL=0.7*FC" in command
    assert "acompressor=" in command
    assert "loudnorm=" in command
    assert "LFE" not in command


def test_clean_trace_prints_stream_specifiers_verbatim(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify -vv prints ffmpeg stream specifiers such as :a: as text, not emoji."""
    # Given: A downmix, whose ffmpeg command carries per-stream audio options
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("no_stereo.json")

    # When: Processing the video file with trace output
    run_vidcleaner(args)

    # Then: The traced command shows the audio stream specifier as typed
    output = capsys.readouterr().out
    assert "-c:a:" in output
    assert "🅰" not in output


def test_clean_video_downmix_atmos(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify a channel-based Atmos track above 7.1 is downmixed to stereo."""
    # Given: A file whose only audio is a 12-channel 7.1.4 Atmos track with no stereo
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("atmos_no_stereo.json")

    # When: Processing the video file with downmix requested
    exit_code = run_vidcleaner(args)

    # Then: The Atmos track is downmixed rather than passed through un-downmixed.
    # One audio stream is kept, so the downmix is output audio stream index 1.
    command = ffmpeg_command(mock_ffmpeg)
    output = capsys.readouterr().out
    assert "-map 0:1 -map 0:1" in command
    assert "-c:a:1 aac" in command
    assert "-ac:a:1 2" in command
    assert "pan=stereo|FL=0.7*FC" in command
    assert exit_code == 0
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_does_not_clobber_kept_stream(
    video_path, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify the downmix targets its own output stream, not a kept surround track."""
    # Given: A file with two surround tracks (7.1 + 5.1) and no stereo
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("no_stereo.json")

    # When: Processing the video file with downmix requested
    run_vidcleaner(args)

    # Then: The two kept streams are output audio 0 and 1, and the downmix codec/filter
    # options bind to index 2, so no encode option lands on the copied surround tracks.
    command = ffmpeg_command(mock_ffmpeg)
    assert "-c:a:0 copy" in command
    assert "-c:a:1 copy" in command
    assert "-filter:a:2" in command
    assert "-filter:a:0" not in command
    assert "-filter:a:1" not in command


def test_clean_reorganize_streams(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify streams in the wrong order are remapped video-first in the single pass."""
    # Given: a file whose video stream is not first
    args = ["clean", "-vv", str(video_path)]
    mock_video_probe("wrong_order.json")

    # When: Processing the video file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: one ffmpeg pass maps the streams in video-first order
    command = ffmpeg_command(mock_ffmpeg)
    assert exit_code == 0
    assert "-map 0:2 -map 0:1 -map 0:3" in command
    assert f"✔ {REORDER_STREAMS_LABEL}" in output
    assert "✔ Saved to" in output


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
    video_path,
    mock_ffmpeg,
    capsys,
    args,
    command_expected,
    conversion_output,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify codec conversion happens in the same single ffmpeg pass as stream selection."""
    # Given: reference.json probe data and a conversion flag
    args = ["clean", "-vv", *args, str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch.object(TempFile, "new_tmp_path", return_value=video_path)
    mocker.patch.object(TempFile, "latest_temp_path", return_value=video_path)

    # When: Processing the video file
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: ffmpeg runs exactly once with selection and conversion combined
    command = ffmpeg_command(mock_ffmpeg)
    for fragment in command_expected:
        assert fragment in command

    # And: the result tree itemizes the conversion, proving the codec change ran alongside
    # stream selection in the same pass
    assert exit_code == 0
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert conversion_output in output


@pytest.mark.parametrize(
    ("verbosity", "skip_shown"),
    [pytest.param([], False, id="normal"), pytest.param(["-vv"], True, id="-vv")],
)
def test_clean_h265_skip_shown_only_in_debug_mode(
    video_path,
    capsys,
    mock_ffmpeg,
    mock_video_probe,
    verbosity,
    skip_shown,
    mock_original_language,
    stub_output_write,
):
    """Verify a skipped --h265 request and its reason appear only with -vv."""
    # Given: reference.json rewritten so the video stream is already HEVC
    box = mock_video_probe("reference.json")
    box.streams[0].codec_name = "hevc"

    # When: running clean with --h265 requested
    exit_code = run_vidcleaner(["clean", *verbosity, "--h265", str(video_path)])

    # Then: the result tree renders, and lists the skip only in debug mode
    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"✔ {DROP_AUDIO_LABEL}" in output
    skip_line = f"✖ {CONVERT_TO_H265_LABEL}  ({ALREADY_MODERN_CODEC_REASON})"
    assert (skip_line in output) is skip_shown


def test_clean_renders_completed_steps_on_error(
    mocker, mock_ffmpeg, capsys, video_path, mock_video_probe, mock_original_language
) -> None:
    """Verify operations render up front even when the later write step raises.

    A write failure is a per-file failure like any other: it is reported and the batch
    exits non-zero rather than propagating raw, since a single write error must not be
    able to look different from any other file-level failure to the caller.
    """
    # Given: operations render up front inside clean(), before the write step, so a
    # failing write can't hide what already completed
    args = ["clean", "-vv", "--h265", str(video_path)]
    mock_video_probe("reference.json")
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=RuntimeError("boom"),
    )

    # When: running clean and the output write raises after processing completes
    exit_code = run_vidcleaner(args)

    # Then: the operations render up front, before the failing write, proving the CLI
    # reports what actually happened even when `write_output()` raises. reference.json's
    # video stream is already first, so reorder is skipped and H.265 conversion is the
    # operation that proves clean() ran to completion. The write failure is reported and
    # the command exits non-zero rather than crashing.
    output, error = capsys.readouterr()
    assert exit_code == 1
    assert "✔ Convert to H.265" in output
    assert f"✖ {REORDER_STREAMS_LABEL}  ({STREAMS_IN_ORDER_REASON})" in output
    assert "1 file(s) failed" in output + error


def test_clean_video_downmix_skip_when_stereo_exists(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify --downmix without --force skips and notifies when a stereo track exists."""
    # Given: reference.json keeps a 7.1, a 5.1, and a non-commentary stereo track
    args = ["clean", "-vv", "--downmix", str(video_path)]
    mock_video_probe("reference.json")

    # When: Processing with downmix requested but not forced
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out
    command = ffmpeg_command(mock_ffmpeg)

    # Then: The existing stereo track is kept and no downmix filter is added
    assert exit_code == 0
    assert "-map 0:4" in command
    assert "-filter:a:" not in command
    assert f"✖ {DOWNMIX_STEREO_LABEL}  ({STEREO_EXISTS_REASON})" in output


def test_clean_video_downmix_force_recreates_stereo(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify --downmix --force drops the existing stereo track and rebuilds from surround."""
    # Given: reference.json with a non-commentary stereo track at index 4
    args = ["clean", "-vv", "--downmix", "--force", str(video_path)]
    mock_video_probe("reference.json")

    # When: Processing with downmix forced
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out
    command = ffmpeg_command(mock_ffmpeg)

    # Then: The old stereo (index 4) is dropped and a fresh downmix is built from the 5.1
    # (index 2). Two audio streams remain mapped, so the downmix is output audio index 2.
    assert exit_code == 0
    assert "-map 0:0 -map 0:1 -map 0:2 -map 0:2" in command
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2 -b:a:2 256k -ar:a:2 48000" in command
    assert "-map 0:4" not in command
    # reference.json's English subtitle matches langs_to_keep and the original language,
    # so it is dropped rather than kept, and the title carries no subtitle flag
    assert "-map 0:6" not in command
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_force_recreates_from_multiple_stereo(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify --downmix --force drops every existing stereo track and rebuilds one from surround."""
    # Given: two non-commentary stereo tracks plus a 5.1 surround bed
    args = ["clean", "-vv", "--downmix", "--force", str(video_path)]
    mock_video_probe("two_stereo_and_surround.json")

    # When: Forcing downmix with two stereo tracks present
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out
    command = ffmpeg_command(mock_ffmpeg)

    # Then: Both stereo tracks (index 2 and 3) are dropped and one downmix is rebuilt from the
    # 5.1 (index 1). Only the 5.1 remains mapped, so the downmix is output audio index 1.
    assert exit_code == 0
    assert "-map 0:2" not in command
    assert "-map 0:3" not in command
    assert "-map 0:1 -map 0:1" in command
    assert "-c:a:1 aac" in command
    assert "-ac:a:1 2 -b:a:1 256k" in command
    assert "✔ Downmix to stereo" in output


def test_clean_video_downmix_force_no_surround_keeps_stereo(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify --downmix --force keeps the existing stereo and notifies when no surround exists."""
    # Given: A file whose only audio track is a non-commentary stereo track
    args = ["clean", "-vv", "--downmix", "--force", str(video_path)]
    mock_video_probe("stereo_no_surround.json")

    # When: Forcing downmix with no surround source to rebuild from
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The stereo track is kept (all streams pass through) and the user is notified.
    # Under -vv every skipped operation is shown (none applied), including the downmix
    # itself, rather than the normal-mode "No changes needed" line.
    assert exit_code == 0
    assert "✖ Downmix to stereo  (no surround source to downmix)" in output


def test_clean_video_downmix_force_noop_without_existing_stereo(
    video_path,
    capsys,
    mock_ffmpeg,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
):
    """Verify --force does not change the normal downmix path when no stereo track exists."""
    # Given: no_stereo.json (7.1 + 5.1 + a stereo commentary track only)
    args = ["clean", "-vv", "--downmix", "--force", str(video_path)]
    mock_video_probe("no_stereo.json")

    # When: Forcing downmix with no existing stereo mix
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out
    command = ffmpeg_command(mock_ffmpeg)

    # Then: The downmix is built from the 5.1 exactly as an un-forced --downmix would
    assert exit_code == 0
    assert "-c:a:2 aac" in command
    assert "-ac:a:2 2 -b:a:2 256k" in command
    assert "-filter:a:2" in command
    assert "✔ Downmix to stereo" in output


def test_clean_renders_one_tree_per_file(
    video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language, stub_output_write
):
    """Verify a file's operations and outcomes close a single tree, not one each."""
    # Given: A file whose operations render up front and whose write then reports a save
    args = ["clean", "-vv", "--h265", str(video_path)]
    mock_video_probe("reference.json")

    # When: Running clean
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: Everything printed for the file closes a single tree, on its last line
    assert exit_code == 0
    per_file = output.rsplit("\u21e8", 1)[-1]
    assert per_file.count(TREE_LAST) == 1
    assert TREE_LAST in per_file.splitlines()[-1]


def test_clean_debug_output_reports_unusable_language_tags(
    mock_video_probe, video_path, capsys, mock_ffmpeg, mock_original_language
):
    """Verify -vv names a language tag that planning treated as unknown."""
    # Given: reference.json with the French 5.1 track tagged with a reserved code
    mock_video_probe("reference.json").streams[3].language = "qaa"

    # When: Dry-running clean with debug output
    exit_code = run_vidcleaner(["-n", "-vv", "clean", str(video_path)])

    # Then: The debug output names the stream and its unusable tag
    assert exit_code == 0
    assert "Stream #3: unusable language tag 'qaa'" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("is_terminal", "answer", "expect_scaled"),
    [
        pytest.param(True, True, True, id="approved upscale runs"),
        pytest.param(True, False, False, id="declined upscale is skipped"),
        pytest.param(True, EOFError(), False, id="closed stdin skips the upscale"),
        pytest.param(False, None, False, id="non-interactive skips the upscale"),
    ],
)
def test_clean_asks_before_upscaling_to_1080p(
    mocker,
    video_path,
    capsys,
    mock_ffmpeg,
    interactive_console,
    mock_video_probe,
    mock_original_language,
    stub_output_write,
    is_terminal,
    answer,
    expect_scaled,
):
    """Verify --1080p --force on a smaller video warns and upscales only when approved."""
    # Given: a 1280px-wide video and a user who answers the upscale prompt, if asked
    probe = mock_video_probe("reference.json")
    probe.streams[0].width = 1280
    interactive_console(is_terminal=is_terminal)
    mocker.patch("vid_cleaner.cli.clean_video.Confirm.ask", side_effect=[answer])

    # When: cleaning the file
    exit_code = run_vidcleaner(["-vv", "clean", "--1080p", "--force", str(video_path)])

    # Then: the user was warned, and the video is scaled only when the upscale went ahead
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 0
    assert "1280px wide" in output
    assert ("scale=width=1920" in ffmpeg_command(mock_ffmpeg)) is expect_scaled
    assert (UPSCALE_DECLINED_REASON in output) is not expect_scaled


def test_clean_dryrun_warns_about_an_upscale_without_asking(
    mocker, video_path, capsys, mock_ffmpeg, mock_video_probe, mock_original_language
):
    """Verify a dry run of --1080p --force on a smaller video previews the upscale silently."""
    # Given: a 1280px-wide video
    probe = mock_video_probe("reference.json")
    probe.streams[0].width = 1280
    ask = mocker.patch("vid_cleaner.cli.clean_video.Confirm.ask")

    # When: previewing the clean
    exit_code = run_vidcleaner(["-n", "-vv", "clean", "--1080p", "--force", str(video_path)])

    # Then: the warning and the planned scale are shown, with no prompt and no ffmpeg run
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 0
    assert "1280px wide" in output
    assert "scale=width=1920" in output
    ask.assert_not_called()
    mock_ffmpeg.assert_not_called()
