# type: ignore
"""Test the vidcleaner clean subcommand."""

from pathlib import Path

import cappa
import pytest
from iso639 import Lang

from vid_cleaner.constants import TREE_LAST
from vid_cleaner.exceptions import VideoCleanError
from vid_cleaner.models import video_file as video_file_module
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand

from vid_cleaner.models.video_file import VideoFile  # isort: skip
from vid_cleaner.controllers import TempFile  # isort: skip
from vid_cleaner import settings


@pytest.fixture(autouse=True)
def set_default_settings(tmp_path, mocker):
    """Set default settings for tests."""
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
        }
    )


def test_fail_on_flag_conflict(debug, tmp_path, capsys, mock_video_path) -> None:
    """Verify clean command fails when incompatible flags are used."""
    # Given: Conflicting codec conversion flags
    args = ["clean", "-vv", "--h265", "--vp9", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # When: Running clean command with conflicting flags
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Error message is displayed
    output = capsys.readouterr().err

    assert exc_info.value.code == 1
    assert "Cannot convert to both H265 and VP9" in output


def test_clean_out_with_multiple_files_errors(debug, tmp_path, capsys) -> None:
    """Verify clean command rejects --out when given more than one input file."""
    # Given: an explicit --out alongside two input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()
    args = ["clean", "-vv", "--out", str(tmp_path / "out.mkv"), str(first), str(second)]

    # When: running clean with --out and multiple files
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: the command exits with an error explaining the conflict
    output = capsys.readouterr().err
    assert exc_info.value.code == 1
    assert "`--out` cannot be used with multiple input files" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            ["✔ Drop unwanted audio", "✔ Drop unwanted subtitles"],
            id="Defaults (only keep local audio,no commentary)",
        ),
        pytest.param(
            ["--downmix"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            ["✖ Downmix to stereo  (stereo track already exists; use --force)"],
            id="Don't convert audio to stereo when stereo exists",
        ),
        pytest.param(
            ["--keep-commentary"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:5"],
            ["✔ Drop unwanted audio"],
            id="Keep commentary",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            ["✔ Drop unwanted audio"],
            id="Keep local language from config even when dropped",
        ),
        pytest.param(
            ["--langs", "fr,es"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:8"],
            ["✔ Drop unwanted subtitles"],
            id="Keep specified languages",
        ),
        pytest.param(
            ["--keep-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6 -map 0:7 -map 0:8"],
            ["✖ Drop unwanted subtitles  (--keep-all-subtitles set)"],
            id="Keep all subtitles",
        ),
        pytest.param(
            ["--keep-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            ["✔ Drop unwanted subtitles"],
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
    assert "✖ Reorder streams  (streams already in order)" in output
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
    assert "✖ Reorder streams  (streams already in order)" in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:6"],
            ["✔ Drop unwanted subtitles"],
            id="Defaults keep local and original audio, local subs",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            ["✔ Drop unwanted audio", "✔ Drop unwanted subtitles"],
            id="Drop original audio (keeps local audio)",
        ),
        pytest.param(
            ["--drop-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4"],
            ["✔ Drop unwanted subtitles"],
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
    assert "✖ Reorder streams  (streams already in order)" in output
    for fragment in process_output:
        assert fragment in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2"],
            "✔ Drop unwanted audio",
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
    assert "✖ Reorder streams  (streams already in order)" in output
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
    args = ["clean", "-vv", "--downmix", str(mock_video_path)]
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: It completes successfully without raising an unexpected error. Under -vv every
    # skipped operation is shown (none applied), not the normal-mode "No changes needed" line.
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "✖ Reorder streams  (streams already in order)" in output
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The dialogue-forward chain is applied, the center is dominant, and LFE is dropped
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert "pan=stereo|FL=0.8*FC" in command
    assert "acompressor=" in command
    assert "loudnorm=" in command
    assert "LFE" not in command


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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The Atmos track is downmixed rather than passed through un-downmixed.
    # One audio stream is kept, so the downmix is output audio stream index 1.
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    output = capsys.readouterr().out
    assert "-map 0:1 -map 0:1" in command
    assert "-c:a:1 aac" in command
    assert "-ac:a:1 2" in command
    assert "pan=stereo|FL=0.8*FC" in command
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: one ffmpeg pass maps the streams in video-first order
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])
    assert exc_info.value.code == 0
    assert "-map 0:2 -map 0:1 -map 0:3" in command
    assert "✔ Reorder streams" in output
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
    assert "✖ Reorder streams  (streams already in order)" in output
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: the skipped H.265 conversion is reported with its reason
    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "✖ Convert to H.265  (already H.265/VP9; use --force)" in output


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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: normal mode is genuinely active and the skipped operation is not shown
    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert settings.verbosity == 0
    assert "✖ Convert to H.265" not in output
    assert "already H.265/VP9; use --force" not in output
    # But verify that rendering actually happened (positive assertion)
    assert "✔ Drop unwanted audio" in output


def test_clean_multiple_files_use_distinct_output_paths(
    mocker,
    mock_ffprobe_box,
    mock_ffmpeg,
    capsys,
    tmp_path,
) -> None:
    """Verify each input file is written to its own output path, not the first file's."""
    # Given: two distinct input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()

    args = ["clean", "-vv", "--downmix", str(first), str(second)]

    # And: mocked external dependencies; copy_file echoes the destination it was handed
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mock_copy = mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: cleaning both files in a single invocation
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: each file is copied to its own destination, not both to the first file's path
    assert exc_info.value.code == 0
    destinations = [call.args[1] for call in mock_copy.mock_calls]
    assert destinations == [first.resolve(), second.resolve()]


def test_clean_multiple_files_overwrite_each_in_place(
    mocker,
    mock_ffprobe_box,
    mock_ffmpeg,
    capsys,
    tmp_path,
) -> None:
    """Verify each file overwrites itself with --overwrite, leaving no original deleted."""
    # Given: two distinct input files
    first = Path(tmp_path / "first_video.mkv")
    first.touch()
    second = Path(tmp_path / "second_video.mkv")
    second.touch()

    args = ["clean", "-vv", "--overwrite", "--downmix", str(first), str(second)]

    # And: mocked external dependencies; copy_file echoes the destination it was handed
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mock_copy = mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: cleaning both files in place
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: each file overwrites itself and neither original is deleted
    assert exc_info.value.code == 0
    destinations = [call.args[1] for call in mock_copy.mock_calls]
    assert destinations == [first.resolve(), second.resolve()]
    assert first.exists()
    assert second.exists()


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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: the operations render up front, before the failing write, proving the CLI
    # reports what actually happened even when `write_output()` raises. reference.json's
    # video stream is already first, so reorder is skipped and H.265 conversion is the
    # operation that proves clean() ran to completion. The write failure is reported and
    # the command exits non-zero rather than crashing.
    output, error = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "✔ Convert to H.265" in output
    assert "✖ Reorder streams  (streams already in order)" in output
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out
    mock_ffmpeg.assert_called_once()
    call_args, _ = mock_ffmpeg.call_args
    command = " ".join(call_args[0])

    # Then: The existing stereo track is kept and no downmix filter is added
    assert exc_info.value.code == 0
    assert "-map 0:4" in command
    assert "-filter:a:" not in command
    assert "Stereo track already exists" in output


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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The stereo track is kept (all streams pass through) and the user is notified.
    # Under -vv every skipped operation is shown (none applied), including the downmix
    # itself, rather than the normal-mode "No changes needed" line.
    assert exc_info.value.code == 0
    assert "No surround source to recreate stereo" in output
    assert "✖ Downmix to stereo  (no surround source to downmix)" in output


def test_clean_video_downmix_force_noop_without_existing_stereo(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
    debug,
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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

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


# ---------------------------------------------------------------------------
# VideoFile._find_original_language orchestration
#
# These call _find_original_language() directly rather than through the CLI,
# mocking at the query_tmdb/query_tmdb_by_id/_query_arr_apps_for_imdb_id
# boundary so the real ID-discovery and fallback ordering is exercised.
# ---------------------------------------------------------------------------


def test_find_original_language_skips_arr_when_filename_id_resolves(
    mocker, tmp_path, mock_probe_tags
) -> None:
    """Verify Radarr/Sonarr are not queried once a filename-derived ID resolves."""
    # Given: a filename carrying a resolvable IMDb ID and no container tags
    path = tmp_path / "Some Movie (1999) tt0133093.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.models.video_file.query_tmdb",
        return_value={"movie_results": [{"original_language": "en"}]},
    )
    mock_query_arr = mocker.patch.object(VideoFile, "_query_arr_apps_for_imdb_id")

    # When: finding the original language
    result = video_file._find_original_language()  # noqa: SLF001

    # Then: the filename's IMDb ID resolves the language and Radarr/Sonarr are never queried
    assert result == Lang("en")
    mock_query_tmdb.assert_called_once_with("tt0133093")
    mock_query_arr.assert_not_called()


def test_find_original_language_consults_arr_when_no_id_resolves(
    mocker, tmp_path, mock_probe_tags
) -> None:
    """Verify Radarr/Sonarr are consulted as a last resort when no ID is discoverable."""
    # Given: a filename and container with no discoverable media ID
    path = tmp_path / "Some Movie (1999).mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.models.video_file.query_tmdb",
        return_value={"movie_results": [{"original_language": "fr"}]},
    )
    mock_query_arr = mocker.patch.object(
        VideoFile, "_query_arr_apps_for_imdb_id", return_value="tt0245712"
    )

    # When: finding the original language
    result = video_file._find_original_language()  # noqa: SLF001

    # Then: Radarr/Sonarr is queried and its IMDb ID resolves the language
    assert result == Lang("fr")
    mock_query_arr.assert_called_once()
    mock_query_tmdb.assert_called_once_with("tt0245712")


def test_find_original_language_caches_result(mocker, tmp_path, mock_probe_tags) -> None:
    """Verify a second call reuses the cached language instead of re-querying."""
    # Given: a filename with a resolvable IMDb ID
    path = tmp_path / "Some Movie (1999) tt0133093.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags()
    mock_query_tmdb = mocker.patch(
        "vid_cleaner.models.video_file.query_tmdb",
        return_value={"movie_results": [{"original_language": "en"}]},
    )

    # When: finding the original language twice
    first = video_file._find_original_language()  # noqa: SLF001
    second = video_file._find_original_language()  # noqa: SLF001

    # Then: both calls return the cached language but only one query was made
    assert first == Lang("en")
    assert second == Lang("en")
    mock_query_tmdb.assert_called_once()


def test_find_original_language_falls_through_failed_candidate(
    mocker, tmp_path, mock_probe_tags
) -> None:
    """Verify a failed first candidate falls through to the next discovered ID."""
    # Given: a filename IMDb ID that fails to resolve and a container TMDB tag that does
    path = tmp_path / "Some Movie (1999) tt9999999.mkv"
    path.touch()
    video_file = VideoFile(path)
    mock_probe_tags({"TMDB": "movie/1399"})
    mock_query_tmdb = mocker.patch("vid_cleaner.models.video_file.query_tmdb", return_value={})
    mock_query_tmdb_by_id = mocker.patch(
        "vid_cleaner.models.video_file.query_tmdb_by_id",
        return_value={"original_language": "en"},
    )

    # When: finding the original language
    result = video_file._find_original_language()  # noqa: SLF001

    # Then: the unresolvable IMDb candidate is skipped and the TMDB candidate resolves
    assert result == Lang("en")
    mock_query_tmdb.assert_called_once_with("tt9999999")
    mock_query_tmdb_by_id.assert_called_once_with(tmdb_id="1399", media_type="movie")


@pytest.mark.parametrize(
    ("radarr_response", "expected"),
    [
        pytest.param(
            {"parsedMovieInfo": {"movieTitle": "Some Movie"}, "movie": {"imdbId": "tt0133093"}},
            "tt0133093",
            id="Radarr matched the movie",
        ),
        pytest.param(
            {"parsedMovieInfo": {"movieTitle": "Some Movie"}},
            None,
            id="Radarr parsed the title but matched no movie",
        ),
        pytest.param(
            {"movie": {"title": "Some Movie"}},
            None,
            id="Radarr matched a movie carrying no imdb id",
        ),
    ],
)
def test_query_arr_apps_for_imdb_id_radarr(mocker, tmp_path, radarr_response, expected) -> None:
    """Verify the Radarr response is read for an IMDb ID without raising."""
    # Given: a video file and a Radarr response
    path = tmp_path / "Some Movie (1999).mkv"
    path.touch()
    video_file = VideoFile(path)
    mocker.patch(
        "vid_cleaner.models.video_file.query_radarr",
        autospec=True,
        return_value=radarr_response,
    )
    mocker.patch("vid_cleaner.models.video_file.query_sonarr", autospec=True, return_value={})

    # When: querying the arr apps for an IMDb ID
    result = video_file._query_arr_apps_for_imdb_id()  # noqa: SLF001

    # Then: the IMDb ID is returned when present, otherwise None
    assert result == expected


def test_clean_rejects_from_with_positional_files(capsys, tmp_path):
    """Verify --from and explicit files are refused together as conflicting sources."""
    # Given: Both a discovery root and an explicit file
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--from", str(tmp_path), str(video)]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors explaining the conflict
    assert exc_info.value.code == 1
    assert "`--from` cannot be combined with explicit file paths" in capsys.readouterr().err


def test_clean_rejects_from_with_out(capsys, tmp_path):
    """Verify --out is refused with --from, since one path cannot name a multi-file selection."""
    # Given: A discovery root and an explicit output path
    args = ["clean", "--from", str(tmp_path), "--out", str(tmp_path / "out.mkv")]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors
    assert exc_info.value.code == 1
    assert "`--out` cannot be used with `--from`" in capsys.readouterr().err


@pytest.mark.parametrize(
    "discovery_arg",
    [["--filters", "h264"], ["--limit", "2"], ["--depth", "1"], ["--reverse"], ["--sort", "size"]],
)
def test_clean_rejects_discovery_flags_without_from(capsys, tmp_path, discovery_arg):
    """Verify a discovery flag without --from errors rather than silently doing nothing."""
    # Given: A discovery flag on an explicit-file run
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", str(video), *discovery_arg]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors naming --from
    assert exc_info.value.code == 1
    assert "require `--from`" in capsys.readouterr().err


def test_clean_rejects_no_files_and_no_from(capsys):
    """Verify clean with neither a file nor --from is a usage error."""
    # Given: No files and no discovery root
    args = ["clean"]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors
    assert exc_info.value.code == 1
    assert "Provide file path(s) or `--from`" in capsys.readouterr().err


def test_clean_from_previews_and_requires_confirmation(capsys, video_library):
    """Verify discovery mode renders the selection table and refuses to run non-interactively."""
    # Given: Two discoverable files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    args = ["clean", "--from", str(directory), "--filters", "h264"]

    # When: Running clean with no TTY and no --yes
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()

    # Then: The table rendered before the refusal, so the user still sees the selection
    assert exc_info.value.code == 1
    assert "apple.mkv" in output
    assert "--yes" in error


def test_clean_from_dryrun_skips_the_prompt(capsys, video_library, mock_ffmpeg):
    """Verify --dryrun previews without prompting, since a preview is not an action."""
    # Given: Two discoverable files
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    args = ["-n", "clean", "--from", str(directory), "--filters", "h264"]

    # When: Running a dry run with no TTY and no --yes
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()

    # Then: It completes successfully without demanding --yes
    assert exc_info.value.code == 0
    assert "apple.mkv" in output
    assert "--yes" not in error


def test_clean_from_limit_selects_top_results(capsys, video_library, mock_ffmpeg):
    """Verify --limit narrows what discovery mode acts on."""
    # Given: Three discoverable files
    directory = video_library(
        [
            ("apple.mkv", 100, 1_000_000),
            ("banana.mkv", 300, 1_000_000),
            ("cherry.mkv", 200, 1_000_000),
        ]
    )
    args = ["-n", "clean", "--from", str(directory), "--sort", "size", "--limit", "1"]

    # When: Previewing the single largest file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Only the largest file is selected
    assert exc_info.value.code == 0
    assert "banana.mkv" in output
    assert "apple.mkv" not in output


def test_clean_continues_after_a_file_fails(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify one failing file does not discard the work queued behind it."""
    # Given: Two files where the first fails to clean
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def clean(self):
        if self.path.name == "first.mkv":
            msg = "no video streams found"
            raise VideoCleanError(path=self.path, reason=msg)
        return []

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", clean)

    # When: Cleaning both files
    args = ["-n", "clean", str(first), str(second)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()

    # Then: The second file was still attempted and the failure is reported at the end
    assert exc_info.value.code == 1
    assert "second.mkv" in output
    assert "first.mkv" in output + error
    assert "1 file(s) failed" in output + error


def test_clean_exits_zero_when_every_file_succeeds(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify a clean run with no failures still exits successfully."""
    # Given: One file that cleans without error
    video = tmp_path / "movie.mkv"
    video.touch()
    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", return_value=[])

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command exits successfully with no failure report
    assert exc_info.value.code == 0
    assert "failed" not in capsys.readouterr().out


def test_clean_keyboard_interrupt_aborts_the_whole_batch(capsys, tmp_path, mocker):
    """Verify Ctrl-C stops the run rather than being collected as a per-file failure."""
    # Given: Two files where the first raises the exit cappa uses for an interrupt
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def clean(self):
        if self.path.name == "first.mkv":
            raise cappa.Exit(code=1)
        return []

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", clean)

    # When: Cleaning both files
    args = ["-n", "clean", str(first), str(second)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The run aborts before reaching the second file
    assert exc_info.value.code == 1
    assert "second.mkv" not in capsys.readouterr().out


def test_clean_write_output_cleanup_failure_still_reports_the_save(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    capsys,
    mock_ffmpeg,
):
    """Verify a cleanup error after a successful write is a warning, not a per-file failure.

    The file is already copied to its destination by the time `TempFile.clean_up()` runs,
    so a failure there must not discard the "Saved to" message or count the file as failed.
    """
    # Given: a file that cleans and writes successfully, but whose temp-directory
    # housekeeping raises after the output is already safely in place
    args = ["clean", "-vv", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, ["✔ Saved to cleaned_video.mkv"]),
    )
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: the run still exits successfully, the save is still reported, and the
    # cleanup failure is surfaced as a warning rather than a batch failure
    assert exc_info.value.code == 0
    assert "cleaned_video.mkv" in output
    assert "temp dir busy" in output
    assert "failed" not in output


def test_clean_rejects_yes_without_from(capsys, tmp_path):
    """Verify --yes without --from errors rather than being accepted as a no-op."""
    # Given: --yes on an explicit-file run, where no prompt would ever be shown
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--yes", str(video)]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors naming --from
    assert exc_info.value.code == 1
    assert "require `--from`" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("short", "long", "attribute"),
    [
        ("-H", "--h265", "h265"),
        ("-V", "--vp9", "vp9"),
        ("-p", "--1080p", "video_1080"),
        ("-d", "--downmix", "downmix_stereo"),
        ("-D", "--drop-original", "drop_original_audio"),
        ("-s", "--keep-subs", "keep_all_subtitles"),
        ("-S", "--keep-local-subs", "keep_local_subtitles"),
        ("-x", "--drop-local-subs", "drop_local_subs"),
        ("-c", "--keep-commentary", "keep_commentary"),
        ("-f", "--force", "force"),
        ("-w", "--overwrite", "overwrite"),
    ],
)
def test_clean_short_flag_matches_long_flag(tmp_path, short, long, attribute):
    """Verify each short flag toggles the same option as its long form."""
    # Given: A clean invocation naming one file
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the short form and the long form of the same option
    from_short = cappa.parse(VidCleaner, argv=["clean", short, str(video)]).command
    from_long = cappa.parse(VidCleaner, argv=["clean", long, str(video)]).command
    unset = cappa.parse(VidCleaner, argv=["clean", str(video)]).command

    # Then: Both set the same field, and to something other than the default
    assert getattr(from_short, attribute) == getattr(from_long, attribute)
    assert getattr(from_short, attribute) != getattr(unset, attribute)


def test_clean_short_flags_bundle(tmp_path):
    """Verify short flags combine into one token so common conversions stay terse."""
    # Given: A clean invocation bundling several boolean short flags
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the bundled form
    command = cappa.parse(VidCleaner, argv=["clean", "-Hdsc", str(video)]).command

    # Then: Every bundled flag is set
    assert command.h265
    assert command.downmix_stereo
    assert command.keep_all_subtitles
    assert command.keep_commentary


def test_clean_langs_short_flag_takes_a_value(tmp_path):
    """Verify -l accepts a language list rather than being treated as a boolean."""
    # Given: A clean invocation passing languages via the short flag
    video = tmp_path / "movie.mkv"
    video.touch()

    # When: Parsing the short form
    command = cappa.parse(VidCleaner, argv=["clean", "-l", "es,fr", str(video)]).command

    # Then: The value lands in langs_to_keep
    assert command.langs_to_keep == "es,fr"


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_clean_rejects_limit_below_one(capsys, tmp_path, limit):
    """Verify a limit below one is refused instead of silently selecting the wrong files."""
    # Given: A discovery run asking for a zero or negative limit
    args = ["clean", "--from", str(tmp_path), "--limit", limit]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command fails with a message naming the acceptable range
    assert exc_info.value.code != 0
    assert "must be 1 or greater" in str(exc_info.value.message)


def test_clean_rejects_from_that_does_not_exist(capsys, tmp_path):
    """Verify a missing --from directory fails instead of reporting a successful no-op."""
    # Given: A --from path that does not exist
    args = ["clean", "--from", str(tmp_path / "does-not-exist")]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors rather than exiting 0
    assert exc_info.value.code == 1
    assert "`--from` must be an existing directory" in capsys.readouterr().err


def test_clean_rejects_from_that_is_a_file(capsys, tmp_path):
    """Verify a --from pointing at a file fails, since clean otherwise takes file paths."""
    # Given: A --from path naming a file rather than a directory
    video = tmp_path / "movie.mkv"
    video.touch()
    args = ["clean", "--from", str(video)]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command errors rather than exiting 0
    assert exc_info.value.code == 1
    assert "`--from` must be an existing directory" in capsys.readouterr().err


def test_clean_from_empty_directory_still_exits_zero(capsys, tmp_path):
    """Verify a real but empty directory remains a successful no-op, not a failure."""
    # Given: An existing directory holding no video files
    directory = tmp_path / "library"
    directory.mkdir()
    args = ["clean", "--from", str(directory)]

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The command reports the empty directory and exits successfully
    assert exc_info.value.code == 0
    assert "No video files found" in capsys.readouterr().err


def test_clean_removes_the_original_even_when_temp_cleanup_fails(
    mocker, mock_ffprobe_box, mock_ffmpeg, capsys, tmp_path
):
    """Verify a failed temp cleanup does not skip removing the original under --overwrite."""
    # Given: --overwrite renaming the result to a new container, and temp housekeeping
    # that raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The original is gone and the cleanup failure is reported as its own warning
    assert exc_info.value.code == 0
    assert not source.exists()
    assert "temp dir busy" in output


def test_clean_reports_a_failed_original_removal_accurately(
    mocker, mock_ffprobe_box, mock_ffmpeg, capsys, tmp_path
):
    """Verify a failed removal of the original is not reported as a temp-file cleanup error."""
    # Given: --overwrite renaming the result to a new container, where removing the
    # original raises
    source = tmp_path / "movie.mkv"
    source.touch()
    args = ["clean", "-vv", "--overwrite", "--vp9", str(source)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(Path, "unlink", side_effect=OSError("read-only file system"))

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: The warning names the original file, not the temporary files
    assert exc_info.value.code == 0
    assert "could not remove the original" in output
    assert "could not clean up temporary files" not in output


def test_clean_out_with_overwrite_leaves_the_input_alone(
    mocker, mock_ffprobe_box, mock_ffmpeg, tmp_path
):
    """Verify `--out` never deletes the input, even when `--overwrite` is also passed.

    `--overwrite` only says "do not keep a backup of what I am replacing". With `--out`
    the input is not being replaced, so removing it would destroy a file the user asked
    to read from, not write to.
    """
    # Given: --overwrite writing the result to a destination the user chose
    source = tmp_path / "movie.mkv"
    source.touch()
    destination = tmp_path / "out.mkv"
    args = ["clean", "--overwrite", "--out", str(destination), str(source)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # When: Running clean
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The input file is still there
    assert exc_info.value.code == 0
    assert source.exists()


def _normalize(text: str) -> str:
    """Collapse Rich's line wrapping so assertions can match whole phrases.

    Returns:
        str: The text with every run of whitespace reduced to a single space.
    """
    return " ".join(text.split())


def test_clean_failure_lines_name_the_file_exactly_once(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify a domain error's failure line carries the short reason, not the full message.

    `VideoCleanError` embeds the absolute path in its own message, so reporting `str(e)`
    under a line already prefixed with the filename names the file twice.
    """
    # Given: One file failing with a domain error and one with a plain RuntimeError
    first = tmp_path / "first.mkv"
    first.touch()
    second = tmp_path / "second.mkv"
    second.touch()

    def clean(self):
        if self.path.name == "first.mkv":
            raise VideoCleanError(path=self.path, reason="no video streams found")
        msg = "boom"
        raise RuntimeError(msg)

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", clean)

    # When: Cleaning both files
    args = ["-n", "clean", str(first), str(second)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()
    combined = _normalize(output + error)

    # Then: The domain failure names the file once, and the plain error still names it
    assert exc_info.value.code == 1
    assert "first.mkv: no video streams found" in combined
    assert "Could not clean" not in combined
    assert "second.mkv: boom" in combined


def test_clean_clears_the_temp_directory_when_a_file_fails(capsys, tmp_path, mocker, mock_ffmpeg):
    """Verify a failed file's full-size temp transcode is discarded immediately.

    A batch that fails on every file would otherwise hold one full-size intermediate per
    file on disk until the process exits.
    """
    # Given: A file that fails to clean
    video = tmp_path / "movie.mkv"
    video.touch()

    def clean(self):
        raise VideoCleanError(path=self.path, reason="no video streams found")

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", clean)
    cleanup = mocker.patch.object(TempFile, "clean_up")

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The temp directory was cleared as part of handling the failure
    assert exc_info.value.code == 1
    cleanup.assert_called_once()


def test_clean_from_cleanup_failure_does_not_mask_the_real_failure(
    capsys, tmp_path, mocker, mock_ffmpeg
):
    """Verify a cleanup error while handling a failure does not replace the reported cause."""
    # Given: A file that fails to clean and whose temp cleanup then also fails
    video = tmp_path / "movie.mkv"
    video.touch()

    def clean(self):
        raise VideoCleanError(path=self.path, reason="no video streams found")

    mocker.patch("vid_cleaner.models.video_file.VideoFile.clean", clean)
    mocker.patch.object(TempFile, "clean_up", side_effect=OSError("temp dir busy"))

    # When: Cleaning it
    args = ["-n", "clean", str(video)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output, error = capsys.readouterr()
    combined = _normalize(output + error)

    # Then: The original cause is still what the batch reports
    assert exc_info.value.code == 1
    assert "movie.mkv: no video streams found" in combined


def test_clean_from_yes_reaches_the_transcode_loop(capsys, video_library, mocker, mock_ffmpeg):
    """Verify --yes skips the prompt and actually transcodes the discovered selection."""
    # Given: Two discoverable files and a stubbed writer
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    args = ["clean", "--from", str(directory), "--filters", "h264", "--yes"]

    # When: Running clean with --yes and no TTY
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Both files were transcoded and written
    assert exc_info.value.code == 0
    assert mock_ffmpeg.call_count == 2
    assert "apple.mkv" in output
    assert "banana.mkv" in output


def test_clean_from_yes_reuses_the_discovery_probe(capsys, video_library, mocker, mock_ffmpeg):
    """Verify each file is probed once, not again when the transcode loop reaches it.

    Discovery already probed every candidate and `VideoFile` caches the result, so
    `select_video_files` hands back those instances. Rebuilding them from paths would
    silently double the dominant cost of a large `--from` run.
    """
    # Given: Two discoverable files and a stubbed writer
    directory = video_library([("apple.mkv", 100, 1_000_000), ("banana.mkv", 200, 1_000_000)])
    probe = video_file_module.get_probe_as_box
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_to_output",
        side_effect=lambda src, dst, *, overwrite: (dst, [f"✔ Saved to {dst}"]),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    args = ["clean", "--from", str(directory), "--filters", "h264", "--yes"]

    # When: Running clean with --yes
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: ffprobe ran exactly once per file
    assert exc_info.value.code == 0
    assert probe.call_count == 2


def test_clean_from_declining_the_prompt_changes_nothing(
    capsys, video_library, mocker, mock_ffmpeg, interactive_console
):
    """Verify answering no is a successful no-op that leaves every file untouched."""
    # Given: A discoverable file and a user who declines the prompt
    directory = video_library([("apple.mkv", 100, 1_000_000)])
    video = directory / "apple.mkv"
    original = video.read_bytes()
    interactive_console(is_terminal=True)
    mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=False)
    write = mocker.patch("vid_cleaner.cli.clean_video.copy_to_output")
    args = ["clean", "--from", str(directory), "--filters", "h264"]

    # When: Running clean and declining
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Nothing was transcoded, nothing was written, and the file is unchanged
    assert exc_info.value.code == 0
    mock_ffmpeg.assert_not_called()
    write.assert_not_called()
    assert video.read_bytes() == original


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
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = capsys.readouterr().out

    # Then: Everything printed for the file closes a single tree, on its last line
    assert exc_info.value.code == 0
    per_file = output.rsplit("\u21e8", 1)[-1]
    assert per_file.count(TREE_LAST) == 1
    assert TREE_LAST in per_file.splitlines()[-1]


@pytest.fixture
def fake_transcode(mocker):
    """Replace the ffmpeg pass with one that writes a real temporary file.

    Lets a test exercise the genuine `copy_to_output` write, and so the output path and
    the fate of the original, rather than stubbing the write out.

    Returns:
        Callable[[], None]: Call to install the replacement.
    """

    def _inner() -> None:
        def run_ffmpeg(self, command, title, suffix=None, step=None):
            output_path = self.temp_file.new_tmp_path(suffix=suffix or "", step_name=step or "")
            output_path.write_bytes(b"transcoded")
            self.temp_file.created_temp_file(output_path)
            return [f"✔ {title}"]

        mocker.patch.object(VideoFile, "_run_ffmpeg", run_ffmpeg)

    return _inner


def test_clean_vp9_writes_a_webm_file(mocker, mock_ffprobe_box, capsys, tmp_path, fake_transcode):
    """Verify --vp9 names the output for the container it actually produced."""
    # Given: An .mkv input converted to VP9
    video = tmp_path / "movie.mkv"
    video.touch()
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    fake_transcode()

    # When: Cleaning it to VP9 without --overwrite
    args = ["clean", "--vp9", str(video)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: WebM data lands in a .webm file, the original is untouched, and no pointless
    # backup of a file that was never overwritten is left behind
    assert exc_info.value.code == 0
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert video.exists()
    assert not list(tmp_path.glob("*.bak"))


def test_clean_vp9_overwrite_removes_the_original_container(
    mocker, mock_ffprobe_box, capsys, tmp_path, fake_transcode
):
    """Verify --overwrite removes the source .mkv once the result lands in a .webm."""
    # Given: An .mkv input converted to VP9 with --overwrite
    video = tmp_path / "movie.mkv"
    video.touch()
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    fake_transcode()

    # When: Cleaning it to VP9 in place
    args = ["clean", "--vp9", "--overwrite", str(video)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Only the .webm remains, so the container change does not leave two copies
    assert exc_info.value.code == 0
    assert (tmp_path / "movie.webm").read_bytes() == b"transcoded"
    assert not video.exists()
