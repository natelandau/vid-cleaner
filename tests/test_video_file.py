# type: ignore
"""Test the VideoFile class."""

from pathlib import Path

import pytest
import typer
from iso639 import Lang

from vid_cleaner.models import VideoFile  # Replace with the actual import
from vid_cleaner.utils import console


@pytest.fixture()
def video_file(tmp_path):
    """Fixture to return a VideoFile instance with a specified path."""
    # GIVEN a VideoFile instance with a specified path
    test_path = Path(tmp_path / "test_video.mp4")
    test_path.touch()  # Create a dummy file
    return VideoFile(test_path)


@pytest.fixture()
def mock_ffmpeg(mocker):
    """Fixture to mock the FfmpegProgress class."""
    # Mock the FfmpegProgress class and its behavior
    mock_ffmpeg_progress = mocker.patch(
        "vid_cleaner.models.video_file.FfmpegProgress", autospec=True
    )
    mock_instance = mock_ffmpeg_progress.return_value
    mock_instance.run_command_with_progress.return_value = iter([0, 25, 50, 75, 100])
    return mock_ffmpeg_progress


def test_run_ffmpeg(video_file, mock_ffmpeg):
    """Test the behavior of _run_ffmpeg."""
    # GIVEN a mock for logger and command
    test_command = ["ffmpeg", "-i", str(video_file.path), "-some", "-parameters"]

    # WHEN _run_ffmpeg is called
    output_path = video_file._run_ffmpeg(
        test_command, title="Test FFMPEG", suffix=".mkv", step="test_step"
    )

    # THEN _run_ffmpeg should not actually call the real ffmpeg command
    mock_ffmpeg.assert_called_once()
    # AND the output path should be in the temporary directory with the correct suffix
    assert output_path.parent == video_file.tmp_dir
    assert output_path.suffix == ".mkv"
    # AND the output path should include the step in its name
    assert "test_step" in output_path.name


def test_get_input_and_output_default(video_file):
    """Test the default behavior of _get_input_and_output."""
    # WHEN _get_input_and_output is called with default parameters
    input_path, output_path = video_file._get_input_and_output()

    # THEN the input path should be the path of the video file
    assert input_path == video_file.path
    # AND the output path should be in the temporary directory with the correct suffix
    assert output_path.parent == video_file.tmp_dir
    assert output_path.suffix == video_file.suffix


def test_get_input_and_output_with_suffix(video_file):
    """Test the behavior of _get_input_and_output with a custom suffix."""
    # WHEN _get_input_and_output is called with a custom suffix
    custom_suffix = ".mkv"
    _, output_path = video_file._get_input_and_output(suffix=custom_suffix)

    # THEN the output path should have the custom suffix
    assert output_path.suffix == custom_suffix


def test_get_input_and_output_with_step(video_file):
    """Test the behavior of _get_input_and_output with a custom step."""
    # WHEN _get_input_and_output is called with a custom step
    custom_step = "custom_step"
    _, output_path = video_file._get_input_and_output(step=custom_step)

    # THEN the output path should include the custom step in its name
    assert custom_step in output_path.name


def test_get_input_and_output_with_existing_temp_files(video_file, tmp_path):
    """Test the behavior of _get_input_and_output with existing temporary files."""
    # GIVEN existing temporary files
    existing_temp_file = tmp_path / "existing_temp.mp4"
    existing_temp_file.touch()

    video_file.tmp_files = [existing_temp_file]
    video_file.current_tmp_file = existing_temp_file

    # WHEN _get_input_and_output is called
    input_path, output_path = video_file._get_input_and_output()

    # THEN the input path should be the most recent temporary file
    assert input_path == existing_temp_file
    # AND the output path should be a new temporary file
    assert output_path != existing_temp_file


def test_get_input_and_output_directory_creation(video_file):
    """Test the behavior of _get_input_and_output with a non-existing temporary directory."""
    # WHEN _get_input_and_output is called
    _, _ = video_file._get_input_and_output()

    # THEN the temporary directory should be created
    assert video_file.tmp_dir.exists()


def test_cleanup_removes_temp_files_and_directory(video_file):
    """Test the behavior of cleanup()."""
    # GIVEN a VideoFile instance with a temporary directory and files
    # Creating dummy temporary files and directory
    video_file.tmp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = video_file.tmp_dir / "temp_file.mp4"
    temp_file.touch()
    video_file.tmp_files.append(temp_file)

    # WHEN cleanup method is called
    video_file.cleanup()

    # THEN the temporary directory and all temporary files should be removed
    assert not video_file.tmp_dir.exists()  # Temporary directory should not exist
    for temp_file in video_file.tmp_files:
        assert not temp_file.exists()  # Temporary files should not exist


def test_reorder_streams(video_file, mocker, mock_ffmpeg):
    """Test the behavior of reorder_streams."""
    disordered_streams = [
        {"index": 1, "codec_type": "audio"},
        {"index": 2, "codec_type": "video", "codec_name": "h264"},
        {"index": 0, "codec_type": "subtitle"},
    ]
    mocker.patch.object(video_file, "_get_probe", return_value=disordered_streams)

    # WHEN reorder_streams is called
    video_file.reorder_streams()

    # THEN _run_ffmpeg should be called with the correct arguments to reorder streams
    mock_ffmpeg.assert_called_once()
    args, _ = mock_ffmpeg.call_args
    command = args[0]

    assert command[6] == "0:2"  # Video stream should be first
    assert command[8] == "0:1"  # Audio stream should be second
    assert command[10] == "0:0"  # Subtitle stream should be third


def test_reorder_streams_errors(video_file, mocker):
    """Test the error behavior of reorder_streams."""
    disordered_streams = [
        {"index": 1, "codec_type": "audio"},
        {"index": 0, "codec_type": "subtitle"},
    ]
    mocker.patch.object(video_file, "_get_probe", return_value=disordered_streams)

    # WHEN reorder_streams is called
    with pytest.raises(typer.Exit):
        video_file.reorder_streams()

    disordered_streams = [
        {"index": 2, "codec_type": "video", "codec_name": "h264"},
        {"index": 0, "codec_type": "subtitle"},
    ]
    mocker.patch.object(video_file, "_get_probe", return_value=disordered_streams)

    # WHEN reorder_streams is called
    with pytest.raises(typer.Exit):
        video_file.reorder_streams()


def test_process__subtitles_strip_all(video_file):
    """Test the behavior of process_subtitles with language selection."""
    # GIVEN mock subtitle streams in various languages
    streams = [
        {"index": 0, "codec_type": "subtitle", "tags": {"language": "en"}},
        {"index": 1, "codec_type": "subtitle", "tags": {"language": "es"}},
        {"index": 2, "codec_type": "subtitle", "tags": {"language": "fr"}},
    ]

    # WHEN process_subtitles is called with a language
    assert (
        video_file._process_subtitles(
            streams=streams,
            langs_to_keep=["en"],
            keep_commentary=False,
            keep_all_subtitles=False,
            keep_local_subtitles=False,
            keep_subtitles_if_not_original=False,
        )
        == []
    )


def test_process__subtitles_keep_all(video_file):
    """Test the behavior of process_subtitles with language selection."""
    # GIVEN mock subtitle streams in various languages
    streams = [
        {"index": 0, "codec_type": "subtitle", "tags": {"language": "en"}},
        {"index": 1, "codec_type": "subtitle", "tags": {"language": "es"}},
        {"index": 2, "codec_type": "subtitle", "tags": {"language": "fr"}},
    ]

    # WHEN process_subtitles is called with a language
    command = video_file._process_subtitles(
        streams=streams,
        langs_to_keep=["en"],
        keep_commentary=False,
        keep_all_subtitles=True,
        keep_local_subtitles=False,
        keep_subtitles_if_not_original=False,
    )

    assert command == ["-map", "0:0", "-map", "0:1", "-map", "0:2"]


def test_process__subtitles_keep_local(video_file):
    """Test the behavior of process_subtitles."""
    # GIVEN mock subtitle streams in various languages
    streams = [
        {"index": 0, "codec_type": "subtitle", "tags": {"language": "en"}},
        {"index": 1, "codec_type": "subtitle", "tags": {"language": "en", "title": "Commentary"}},
        {"index": 2, "codec_type": "subtitle", "tags": {"language": "fr"}},
    ]

    # WHEN process_subtitles is called with a language
    command = video_file._process_subtitles(
        streams=streams,
        langs_to_keep=["en"],
        keep_commentary=True,
        keep_all_subtitles=False,
        keep_local_subtitles=True,
        keep_subtitles_if_not_original=False,
    )

    assert command == ["-map", "0:0", "-map", "0:1"]


def test_process__subtitles_strip_commentary(video_file):
    """Test the behavior of process_subtitles."""
    # GIVEN mock subtitle streams in various languages
    streams = [
        {"index": 0, "codec_type": "subtitle", "tags": {"language": "en"}},
        {"index": 1, "codec_type": "subtitle", "tags": {"language": "en", "title": "Commentary"}},
        {"index": 2, "codec_type": "subtitle", "tags": {"language": "fr"}},
    ]

    # WHEN process_subtitles is called with a language
    command = video_file._process_subtitles(
        streams=streams,
        langs_to_keep=["en"],
        keep_commentary=False,
        keep_all_subtitles=False,
        keep_local_subtitles=True,
        keep_subtitles_if_not_original=False,
    )

    assert command == ["-map", "0:0"]


@pytest.mark.parametrize(
    ("lang", "result"),
    [
        ("fr", ["-map", "0:0"]),
        ("en", []),
    ],
)
def test__process_subtitles_keep_not_original(video_file, mocker, lang, result):
    """Test the behavior of process_subtitles."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang(lang))

    # GIVEN mock subtitle streams in various languages
    streams = [
        {"index": 0, "codec_type": "subtitle", "tags": {"language": "en"}},
        {"index": 1, "codec_type": "subtitle", "tags": {"language": "en", "title": "Commentary"}},
        {"index": 2, "codec_type": "subtitle", "tags": {"language": "fr"}},
    ]

    # WHEN process_subtitles is called with a language
    command = video_file._process_subtitles(
        streams=streams,
        langs_to_keep=["en"],
        keep_commentary=False,
        keep_all_subtitles=False,
        keep_local_subtitles=False,
        keep_subtitles_if_not_original=True,
    )

    assert command == result


def test__process_audio_keep_commentary(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 0, "codec_type": "audio", "channels": 2, "tags": {"language": "en"}},
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 6,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 6, "tags": {"language": "fr"}},
        {"index": 5, "codec_type": "audio", "channels": 6, "tags": {"language": "es"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en"],
        drop_original_audio=False,
        keep_commentary=True,
        downmix_stereo=False,
    )

    assert command == (["-map", "0:0", "-map", "0:2", "-map", "0:3", "-map", "0:4"], [])


def test__process_audio_drop_original(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 0, "codec_type": "audio", "channels": 2, "tags": {"language": "en"}},
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 6,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 6, "tags": {"language": "fr"}},
        {"index": 5, "codec_type": "audio", "channels": 6, "tags": {"language": "es"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en"],
        drop_original_audio=True,
        keep_commentary=True,
        downmix_stereo=False,
    )

    assert command == (["-map", "0:0", "-map", "0:2", "-map", "0:3"], [])


def test__process_audio_drop_commentary(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 0, "codec_type": "audio", "channels": 2, "tags": {"language": "en"}},
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 6,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 6, "tags": {"language": "fr"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en", "fr"],
        drop_original_audio=False,
        keep_commentary=False,
        downmix_stereo=False,
    )

    assert command == (["-map", "0:0", "-map", "0:2", "-map", "0:4"], [])


def test__process_audio_downmix_not_needed(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 0, "codec_type": "audio", "channels": 2, "tags": {"language": "en"}},
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 6,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 6, "tags": {"language": "fr"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en"],
        drop_original_audio=False,
        keep_commentary=False,
        downmix_stereo=True,
    )

    assert command == (["-map", "0:0", "-map", "0:2", "-map", "0:4"], [])


def test__process_audio_downmix_6channel(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 6,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 6, "tags": {"language": "fr"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en"],
        drop_original_audio=False,
        keep_commentary=False,
        downmix_stereo=True,
    )

    assert command == (
        ["-map", "0:2", "-map", "0:4"],
        [
            "-map",
            "0:2",
            "-c:a:0",
            "aac",
            "-ac:a:0",
            "2",
            "-b:a:0",
            "256k",
            "-filter:a:0",
            "pan=stereo|FL=FC+0.30*FL+0.30*FLC+0.30*BL+0.30*SL+0.60*LFE|FR=FC+0.30*FR+0.30*FRC+0.30*BR+0.30*SR+0.60*LFE,loudnorm",
            "-ar:a:0",
            "48000",
            "-metadata:s:a:0",
            "title=2.0",
            "-map",
            "0:4",
            "-c:a:1",
            "aac",
            "-ac:a:1",
            "2",
            "-b:a:1",
            "256k",
            "-filter:a:1",
            "pan=stereo|FL=FC+0.30*FL+0.30*FLC+0.30*BL+0.30*SL+0.60*LFE|FR=FC+0.30*FR+0.30*FRC+0.30*BR+0.30*SR+0.60*LFE,loudnorm",
            "-ar:a:1",
            "48000",
            "-metadata:s:a:1",
            "title=2.0",
        ],
    )


def test__process_audio_downmix_8channel(video_file, mocker):
    """Test the behavior of _process_audio."""
    # Mock original language
    mocker.patch.object(video_file, "_find_original_language", return_value=Lang("fr"))

    # GIVEN mock audio streams in various languages
    streams = [
        {"index": 2, "codec_type": "audio", "channels": 8, "tags": {"language": "en"}},
        {
            "index": 3,
            "codec_type": "audio",
            "channels": 8,
            "tags": {"language": "en", "title": "Commentary"},
        },
        {"index": 4, "codec_type": "audio", "channels": 8, "tags": {"language": "fr"}},
    ]

    # WHEN _process_audio is called with a language
    command = video_file._process_audio(
        streams=streams,
        langs_to_keep=["en"],
        drop_original_audio=False,
        keep_commentary=False,
        downmix_stereo=True,
    )

    assert command == (
        ["-map", "0:2", "-map", "0:4"],
        [
            "-map",
            "0:2",
            "-c:a:0",
            "aac",
            "-ac:a:0",
            "2",
            "-b:a:0",
            "256k",
            "-metadata:s:a:0",
            "title=2.0",
            "-map",
            "0:4",
            "-c:a:1",
            "aac",
            "-ac:a:1",
            "2",
            "-b:a:1",
            "256k",
            "-metadata:s:a:1",
            "title=2.0",
        ],
    )


def test__process_video(video_file):
    """Test the behavior of _process_video."""
    # GIVEN mock video streams in various codecs
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "video", "codec_name": "mjpeg"},
        {"index": 2, "codec_type": "video", "codec_name": "png"},
    ]

    # WHEN _process_video is called
    command = video_file._process_video(streams=streams)

    # THEN the command should include the correct arguments to drop the non-video streams
    assert command == ["-map", "0:0"]
