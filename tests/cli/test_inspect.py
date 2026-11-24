"""Test the vidcleaner inspect subcommand."""

import re

from tests.helpers import load_ffprobe_json, run_vidcleaner


def test_inspect_table(capsys, video_path, mock_video_probe):
    """Verify inspect command displays video information in table format."""
    # Given: Mock video file and ffprobe data
    args = ["inspect", str(video_path)]
    mock_video_probe("reference.json")

    # When: Running inspect command
    exit_code = run_vidcleaner(args)

    # Then: Output contains expected table data
    output = capsys.readouterr().out
    assert exit_code == 0
    assert re.search(r"0 │ video +│ h264", output)
    assert re.search(r"9 │ video +│ mjpeg", output)
    assert re.search(r"eng +│ 8 +│ 7.1", output)
    assert re.search(r"1920 +│ 1080 +│ Test", output)


def test_inspect_title_shows_file_size(capsys, video_path, mocker, mock_ffprobe_box):
    """Verify the stream table title reports the file's size."""
    # Given: A video file with a known size on disk
    video_path.write_bytes(b"\0" * 2000)
    # mock_ffprobe_box sets path_to_file to the fixture JSON it reads, not the video
    # under test; point it at video_path so the size read from disk is the 2000
    # bytes just written, matching what get_probe_as_box does for a real probe.
    probe_box = mock_ffprobe_box("reference.json")
    probe_box.path_to_file = video_path
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=probe_box,
    )

    # When: Inspecting the file
    args = ["inspect", str(video_path)]
    exit_code = run_vidcleaner(args)

    output = capsys.readouterr().out

    # Then: The title carries the human readable size
    assert exit_code == 0
    assert "(2.0 kB)" in output


def test_inspect_json(capsys, video_path, mocker):
    """Verify inspect command displays video information in JSON format."""
    # Given: Mock video file and ffprobe data
    args = ["inspect", "--json", str(video_path)]
    mocker.patch(
        "vid_cleaner.models.video_file.run_ffprobe",
        return_value=load_ffprobe_json("reference.json"),
    )

    # When: Running inspect command with JSON flag
    exit_code = run_vidcleaner(args)

    # Then: Output contains expected JSON data
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "'bit_rate': '26192239'," in output
    assert "'channel_layout': '7.1'," in output
    assert "'codec_name': 'hdmv_pgs_subtitle'," in output
