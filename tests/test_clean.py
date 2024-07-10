# type: ignore
"""Test the inspect command."""

import re

import pytest
from iso639 import Lang
from typer.testing import CliRunner

from tests.pytest_functions import strip_ansi
from vid_cleaner.config import VidCleanerConfig
from vid_cleaner.models.video_file import VideoFile
from vid_cleaner.vid_cleaner import app

runner = CliRunner()


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
            "✔ Process file (downmix to stereo)",
            id="Don't convert audio to stereo when stereo exists",
        ),
        pytest.param(
            ["--keep-commentary"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4 -map 0:5",
            "✔ Process file (keep commentary)",
            id="Keep commentary",
        ),
        pytest.param(
            ["--drop-original"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:4",
            "✔ Process file (drop original audio)",
            id="Keep local language from config even when dropped",
        ),
        pytest.param(
            ["--langs", "fr,es"],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:8",
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
def test_clean_video_process_streams(
    mocker,
    mock_ffprobe,
    mock_video,
    mock_config,
    mock_ffmpeg,
    debug,
    args,
    command_expected,
    process_output,
):
    """Test cleaning a video that does not need streams reordered or video converted."""
    # GIVEN a video in english with correct stream order
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clean.tmp_to_output", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # WHEN the clean command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["-vv", "clean", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    mock_ffmpeg.assert_called_once()  # Check that the ffmpeg command was called once
    args, _ = mock_ffmpeg.call_args  # Grab the ffmpeg command arguments
    command = " ".join(args[0])  # Join the arguments into a single string
    # debug("ffmpeg command", command)

    assert result.exit_code == 0
    assert command_expected in command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4 -map 0:6",
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
            "-map 0:0 -map 0:1 -map 0:2 -map 0:3 -map 0:4",
            "✔ Process file",
            id="Drop local subs",
        ),
    ],
)
def test_clean_video_foreign_language(
    mocker,
    mock_ffprobe,
    mock_video,
    mock_config,
    mock_ffmpeg,
    debug,
    args,
    command_expected,
    process_output,
):
    """Test cleaning is in a foreign language."""
    # GIVEN a video in french with correct stream order
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clean.tmp_to_output", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("fr"))

    # WHEN the clean command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["-vv", "clean", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    mock_ffmpeg.assert_called_once()  # Check that the ffmpeg command was called once
    args, _ = mock_ffmpeg.call_args  # Grab the ffmpeg command arguments
    command = " ".join(args[0])  # Join the arguments into a single string
    # debug("ffmpeg command", command)

    assert result.exit_code == 0
    assert command_expected in command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ cleaned_video.mkv" in output


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
    mock_ffprobe,
    mock_video,
    mock_config,
    mock_ffmpeg,
    debug,
    args,
    command_expected,
    process_output,
):
    """Test cleaning a video that does not have a stereo audio stream."""
    # GIVEN a video in english with correct stream order
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("no_stereo.json")
    )
    mocker.patch("vid_cleaner.cli.clean.tmp_to_output", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # WHEN the clean command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["-vv", "clean", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    mock_ffmpeg.assert_called_once()  # Check that the ffmpeg command was called once
    args, _ = mock_ffmpeg.call_args  # Grab the ffmpeg command arguments
    command = " ".join(args[0])  # Join the arguments into a single string
    # debug("ffmpeg command", command)

    assert result.exit_code == 0
    assert command_expected in command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ cleaned_video.mkv" in output


@pytest.mark.parametrize(
    ("args", "first_command_expected", "second_command_expected", "process_output"),
    [
        pytest.param(
            [],
            "-c copy -map 0:2 -map 0:1 -map 0:3 -map 0:0",
            "-map 0:2 -map 0:1 -map 0:3",
            "✔ Process file",
            id="Defaults, reorder streams, then process streams",
        ),
    ],
)
def test_clean_reorganize_streams(
    mocker,
    mock_ffprobe,
    mock_video,
    mock_config,
    mock_ffmpeg,
    debug,
    args,
    first_command_expected,
    second_command_expected,
    process_output,
):
    """Test cleaning a video that does not have streams in the right order."""
    # GIVEN a video with the streams out of order
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("wrong_order.json")
    )
    mocker.patch("vid_cleaner.cli.clean.tmp_to_output", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))

    # WHEN the clean command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["-vv", "clean", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    assert mock_ffmpeg.call_count == 2
    # debug("ffmpeg calls", mock_ffmpeg.mock_calls)

    first_command = " ".join(mock_ffmpeg.mock_calls[0].args[0])
    # debug("first command", first_command)

    second_command = " ".join(mock_ffmpeg.mock_calls[2].args[0])
    # debug("second command", second_command)

    assert result.exit_code == 0
    assert first_command_expected in first_command
    assert second_command_expected in second_command
    assert "✔ Reorder streams" in output
    assert process_output in output
    assert "✅ cleaned_video.mkv" in output


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
    mock_ffprobe,
    mock_video,
    mock_config,
    mock_ffmpeg,
    debug,
    args,
    first_command_expected,
    second_command_expected,
    process_output,
):
    """Test converting a video stream to a different format."""
    # GIVEN a video in english with correct stream order
    mocker.patch(
        "vid_cleaner.models.video_file.ffprobe", return_value=mock_ffprobe("reference.json")
    )
    mocker.patch("vid_cleaner.cli.clean.tmp_to_output", return_value="cleaned_video.mkv")
    mocker.patch.object(VideoFile, "_find_original_language", return_value=Lang("en"))
    mocker.patch.object(
        VideoFile, "_get_input_and_output", return_value=(mock_video.path, mock_video.path)
    )

    # WHEN the clean command is invoked
    with VidCleanerConfig.change_config_sources(mock_config()):
        result = runner.invoke(app, ["-vv", "clean", *args, str(mock_video.path)])

    output = strip_ansi(result.output)
    # debug("result", output)

    # THEN the video should be clipped
    assert mock_ffmpeg.call_count == 2
    # debug("ffmpeg calls", mock_ffmpeg.mock_calls)

    first_command = " ".join(mock_ffmpeg.mock_calls[0].args[0])
    # debug("first command", first_command)

    second_command = " ".join(mock_ffmpeg.mock_calls[2].args[0])
    # debug("second command", second_command)

    assert result.exit_code == 0
    assert first_command_expected in first_command
    assert second_command_expected in second_command
    assert "✔ No streams to reorder" in output
    assert process_output in output
    assert "✅ cleaned_video.mkv" in output
