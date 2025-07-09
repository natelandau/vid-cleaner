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
            "downmix_stereo": False,
            "keep_local_subtitles": False,
            "keep_commentary": False,
            "drop_local_subs": False,
            "keep_all_subtitles": False,
            "drop_original_audio": False,
            "save_each_step": False,
        }
    )


def test_fail_on_flag_conflict(debug, tmp_path, clean_stdout, mock_video_path):
    """Verify clean command fails when incompatible flags are used."""
    # Given: Conflicting codec conversion flags
    args = ["clean", "-vv", "--h265", "--vp9", str(mock_video_path)]
    settings.update({"cache_dir": Path(tmp_path), "langs_to_keep": ["en"]})

    # When: Running clean command with conflicting flags
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Error message is displayed
    output = clean_stdout()

    assert exc_info.value.code == 1
    assert "Cannot convert to both H265 and VP9" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "✔ Process file",
            id="Defaults (only keep local audio,no commentary)",
        ),
        pytest.param(
            ["--downmix"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "✔ Process file (downmix to stereo, drop unwanted subtitles)",
            id="Don't convert audio to stereo when stereo exists",
        ),
        pytest.param(
            ["--keep-commentary"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:5",
            "✔ Process file (keep commentary, drop unwanted subtitles)",
            id="Keep commentary",
        ),
        pytest.param(
            ["--drop-original"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "✔ Process file (drop original audio, drop unwanted subtitles)",
            id="Keep local language from config even when dropped",
        ),
        pytest.param(
            ["--langs", "fr,es"],
            "-map 0:0 -map 0:3 -map 0:8",
            "✔ Process file (drop unwanted subtitles)",
            id="Keep specified languages",
        ),
        pytest.param(
            ["--keep-subs"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6 -map 0:7 -map 0:8",
            "✔ Process file (keep subtitles)",
            id="Keep all subtitles",
        ),
        pytest.param(
            ["--keep-local-subs"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6",
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
    clean_stdout,
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
    mocker.patch("vid_cleaner.cli.clean_video.copy_file", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("en")])

    # When: Running clean command
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # Then: FFmpeg is called with correct stream mapping
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    assert command_expected in command

    # And: Success messages are displayed
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ Success: cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6",
            "✔ Process file (drop unwanted subtitles)",
            id="Defaults keep local and original audio, local subs",
        ),
        pytest.param(
            ["--drop-original"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:6",
            "✔ Process file (drop original audio, drop unwanted subtitles)",
            id="Drop original audio (keeps local audio)",
        ),
        pytest.param(
            ["--drop-local-subs"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "✔ Process file",
            id="Drop local subs",
        ),
    ],
)
def test_clean_video_foreign_language(
    mocker,
    mock_video_path,
    clean_stdout,
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
    mocker.patch("vid_cleaner.cli.clean_video.copy_file", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("fr")])

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify the ffmpeg command contains expected stream mappings
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])

    # AND verify the command output indicates successful processing
    assert exc_info.value.code == 0
    assert command_expected in command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ Success: cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-map 0:0 -map 0:1 -map 0:2",
            "✔ Process file",
            id="Defaults, drops commentary",
        ),
        pytest.param(
            ["--downmix"],
            "-map 0:1 -map 0:2 -c copy -map 0:2 -c:a:0 aac -ac:a:0 2 -b:a:0 256k -filter:a:0",
            "✔ Process file (downmix to stereo)",
            id="Defaults",
        ),
    ],
)
def test_clean_video_downmix(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    clean_stdout,
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
    mocker.patch("vid_cleaner.cli.clean_video.copy_file", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("en")])

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # Then: FFmpeg is called with correct stream mapping
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = " ".join(args[0])
    assert command_expected in command

    # And: Success messages are displayed
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ Success: cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "first_command_expected", "second_command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-c copy -map 0:2 -map 0:1 -map 0:3 -map 0:0",
            "-map 0:2 -map 0:1 -map 0:3",
            "✔ No streams to process",
            id="Defaults, reorder streams, then process streams",
        ),
    ],
)
def test_clean_reorganize_streams(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    tmp_path,
    clean_stdout,
    mock_ffmpeg,
    debug,
    args,
    first_command_expected,
    second_command_expected,
    process_output,
):
    """Verify that videos with incorrect stream order are properly reorganized."""
    args = ["clean", "-vv", *args, str(mock_video_path)]

    # And: Mock external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("wrong_order.json"),
    )
    mocker.patch("vid_cleaner.cli.clean_video.copy_file", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("en")])

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg is called once - once to reorder streams, stream processing is skipped
    assert mock_ffmpeg.call_count == 1

    # AND verify the first ffmpeg command contains expected stream reordering
    first_command = " ".join(mock_ffmpeg.mock_calls[0].args[0])
    assert first_command_expected in first_command

    # AND verify the command output indicates successful processing
    assert exc_info.value.code == 0
    assert "✔ Reorder streams" in output
    assert process_output in output
    assert "✅ Success: cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "first_command_expected", "second_command_expected", "process_output"),
    [
        pytest.param(
            ["--h265"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "-map 0 -c:v libx265 -b:v 0k -minrate 0k -maxrate 0k -bufsize 0k -c:a copy -c:s copy",
            "✔ Process file",
            id="Convert to h265",
        ),
        pytest.param(
            ["--vp9"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "-map 0 -c:v libvpx-vp9 -b:v 0 -crf 30 -c:a libvorbis -dn -map_chapters -1 -c:s copy",
            "✔ Process file",
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
    clean_stdout,
    debug,
    args,
    first_command_expected,
    second_command_expected,
    process_output,
):
    """Verify video stream conversion with different codecs."""
    args = ["clean", "-vv", *args, str(mock_video_path)]
    # And: Mock external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch("vid_cleaner.cli.clean_video.copy_file", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("en")])
    mocker.patch.object(TempFile, "new_tmp_path", return_value=(mock_video_path))
    mocker.patch.object(TempFile, "latest_temp_path", return_value=(mock_video_path))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg executes two passes
    assert mock_ffmpeg.call_count == 2

    # AND verify stream mapping command is correct
    first_command = " ".join(mock_ffmpeg.mock_calls[0].args[0])
    assert first_command_expected in first_command

    # AND verify codec conversion command is correct
    second_command = " ".join(mock_ffmpeg.mock_calls[2].args[0])
    assert second_command_expected in second_command

    # AND verify successful completion with expected output messages
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert process_output in output

    if "--vp9" in args:
        assert "Converting to VP9, setting output to test_video.webm" in output
        assert "✔ Convert to vp9" in output
    else:
        assert "✔ Convert to H.265" in output


def test_save_each_step(
    mocker,
    mock_ffprobe_box,
    mock_video_path,
    mock_ffmpeg,
    clean_stdout,
    tmp_path,
    debug,
):
    """Verify video stream conversion with different codecs."""
    # GIVEN a second video file used to mock the output of the first video file
    mock_video_path_1 = Path(tmp_path / "test_video_1.mp4")
    mock_video_path_1.touch()  # Create a dummy file

    args = ["clean", "-vv", "--save-each", "--h265", "--downmix", str(mock_video_path)]
    # And: Mock external dependencies
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    mocker.patch(
        "vid_cleaner.cli.clean_video.copy_file",
        side_effect=[mock_video_path_1, "cleaned_video.mkv"],
    )
    mocker.patch.object(VideoFile, "_find_original_language", return_value=[Lang("en")])
    mocker.patch.object(TempFile, "new_tmp_path", return_value=(mock_video_path))
    mocker.patch.object(TempFile, "latest_temp_path", return_value=(mock_video_path))

    # When: Processing the video file
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    output = clean_stdout()
    # debug(output, "output")

    # THEN verify ffmpeg executes two passes
    assert mock_ffmpeg.call_count == 2

    # AND verify stream mapping command is correct
    first_command = " ".join(mock_ffmpeg.mock_calls[0].args[0])
    assert "-map 0:0 -map 0:1 -map 0:2 -map 0:4" in first_command

    # AND verify codec conversion command is correct
    second_command = " ".join(mock_ffmpeg.mock_calls[2].args[0])
    assert (
        "-map 0 -c:v libx265 -b:v 0k -minrate 0k -maxrate 0k -bufsize 0k -c:a copy -c:s copy"
        in second_command
    )

    # AND verify successful completion with expected output messages
    assert exc_info.value.code == 0
    assert "✔ No streams to reorder" in output
    assert "✔ Process file" in output
    assert "✅ Success: cleaned_video.mkv" in output
    assert "✔ Process file (downmix to stereo, drop unwanted subtitles)" in output
    assert "✅ Success: …/test_video_1.mp4" in output
    assert "✔ Convert to H.265" in output
    assert "✅ Success: cleaned_video.mkv" in output
