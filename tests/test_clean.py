# type: ignore
"""Test the vidcleaner clean subcommand."""

from pathlib import Path

import cappa
import pytest
from iso639 import Lang

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
            "save_each_step": False,
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
            "✔ Process file",
            id="Defaults (only keep local audio,no commentary)",
        ),
        pytest.param(
            ["--downmix"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            "✔ Process file (downmix to stereo)",
            id="Don't convert audio to stereo when stereo exists",
        ),
        pytest.param(
            ["--keep-commentary"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:5"],
            "✔ Process file (keep commentary)",
            id="Keep commentary",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4"],
            "✔ Process file (drop original audio)",
            id="Keep local language from config even when dropped",
        ),
        pytest.param(
            ["--langs", "fr,es"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:8"],
            "✔ Process file (drop unwanted subtitles)",
            id="Keep specified languages",
        ),
        pytest.param(
            ["--keep-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6 -map 0:7 -map 0:8"],
            "✔ Process file (keep subtitles)",
            id="Keep all subtitles",
        ),
        pytest.param(
            ["--keep-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            "✔ Process file (drop unwanted subtitles, keep local subtitles)",
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

    # And: Success messages are displayed
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert process_output in output
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
    assert "✔ No streams to reorder" in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:6"],
            "✔ Process file (drop unwanted subtitles)",
            id="Defaults keep local and original audio, local subs",
        ),
        pytest.param(
            ["--drop-original"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6"],
            "✔ Process file (drop original audio, drop unwanted subtitles)",
            id="Drop original audio (keeps local audio)",
        ),
        pytest.param(
            ["--drop-local-subs"],
            ["-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4"],
            "✔ Process file",
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

    # AND verify the command output indicates successful processing
    assert exc_info.value.code == 0
    for fragment in command_expected:
        assert fragment in command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            ["-map 0:0 -map 0:1 -map 0:2"],
            "✔ Process file",
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
            "✔ Process file (downmix to stereo)",
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

    # And: Success messages are displayed
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
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
    assert "✔ Process file (downmix to stereo)" in output


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

    # Then: It completes successfully without raising an unexpected error
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "No streams to process" in output


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
    assert "✔ Process file (downmix to stereo)" in output


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
    assert "✔ Process file" in output
    assert "cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "substep_expected"),
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
            "✔ Convert to vp9",
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
    substep_expected,
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

    # And: the result tree itemizes the conversion
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert "✔ Process file" in output
    assert substep_expected in output
    if "--vp9" in args:
        assert "Converting to VP9, setting output to `test_video.webm`" in output


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
    """Verify the result tree is still rendered when the conversion raises."""
    # Given: mocked metadata and a conversion that fails
    args = ["clean", "--h265", str(mock_video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(VideoFile, "_run_ffmpeg", autospec=True, side_effect=RuntimeError("boom"))

    # When: running clean and the ffmpeg pass raises
    with pytest.raises(RuntimeError):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: the per-file header was printed before the failure
    output = capsys.readouterr().out
    assert "⇨ test_video.mp4" in output


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
    assert "✔ Process file (downmix to stereo)" in output


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
    assert "✔ Process file (downmix to stereo)" in output


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

    # Then: The stereo track is kept (all streams pass through) and the user is notified
    assert exc_info.value.code == 0
    assert "No surround source to recreate stereo" in output
    assert "No streams to process" in output


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
    assert "✔ Process file (downmix to stereo)" in output


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
