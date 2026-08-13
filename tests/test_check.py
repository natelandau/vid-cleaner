# type: ignore
"""Test the vidcleaner check subcommand."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.cli.check_video import display_path
from vid_cleaner.exceptions import VideoProbeError
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.fixture(autouse=True)
def set_default_settings(tmp_path, monkeypatch):
    """Set default settings and run from `tmp_path` so checked files print as bare names.

    Working from the directory under test keeps the printed lines short enough that Rich
    does not soft-wrap them, so assertions can match a whole line.
    """
    cache_dir = Path(tmp_path) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings.update({"cache_dir": cache_dir, "langs_to_keep": ["en"], "filters": set()})
    monkeypatch.chdir(tmp_path)


def test_check_valid_file_exits_zero(capsys, mock_video_path, mocker, mock_ffprobe_box):
    """Verify a readable video file passes and the command exits zero."""
    # Given: A file that probes cleanly and has a video stream
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )

    # When: Checking it
    args = ["check", str(mock_video_path)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: It is reported valid and the run succeeds
    assert exc_info.value.code == 0
    assert "test_video.mp4" in captured.out
    assert "1 valid, 0 invalid" in captured.out


def test_check_invalid_file_exits_one(capsys, mock_video_path, mocker):
    """Verify an unreadable file fails the run with its ffprobe reason."""
    # Given: A file ffprobe cannot read
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(path=mock_video_path, reason="moov atom not found"),
    )

    # When: Checking it
    args = ["check", str(mock_video_path)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The failure carries ffprobe's reason and the run fails
    assert exc_info.value.code == 1
    assert "moov atom not found" in captured.err
    assert "0 valid, 1 invalid" in captured.out


def test_check_reports_every_file_in_a_mixed_batch(capsys, tmp_path, mocker, mock_ffprobe_box):
    """Verify one bad file never hides the verdict on the files beside it."""
    # Given: A good file, a broken file, a missing path, and a non-video file
    good = tmp_path / "good.mkv"
    good.touch()
    broken = tmp_path / "broken.mkv"
    broken.touch()
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    missing = tmp_path / "missing.mkv"

    good_box = mock_ffprobe_box("reference.json")

    def probe(path):
        if path.name == broken.name:
            raise VideoProbeError(path=path, reason="Invalid data found")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Checking all four at once
    args = ["check", str(good), str(broken), str(notes), str(missing)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()
    output = captured.out + captured.err

    # Then: Every file is accounted for and the run fails
    assert exc_info.value.code == 1
    assert "good.mkv" in output
    assert "Invalid data found" in output
    assert "not a video file" in output
    assert "file does not exist" in output
    assert "1 valid, 3 invalid" in captured.out


def test_check_audio_only_file_fails(capsys, mock_video_path, mocker, mock_ffprobe_box):
    """Verify a file that probes cleanly but holds no video stream fails."""
    # Given: A file whose only stream is audio
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("audio_only.json"),
    )

    # When: Checking it
    args = ["check", str(mock_video_path)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: It fails for lacking video
    assert exc_info.value.code == 1
    assert "no video stream" in captured.err


def test_check_deep_decodes_every_file(capsys, mock_video_path, mocker, mock_ffprobe_box):
    """Verify `--deep` runs a full decode over each file."""
    # Given: A file that probes cleanly but fails to decode
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    decode = mocker.patch(
        "vid_cleaner.controllers.validation.decode_check",
        return_value="Error while decoding stream #0:0",
    )

    # When: Checking it deeply
    args = ["check", "--deep", str(mock_video_path)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The decode ran and its error is the reason
    decode.assert_called_once()
    assert exc_info.value.code == 1
    assert "Error while decoding stream #0:0" in captured.err


def test_check_without_deep_does_not_decode(capsys, mock_video_path, mocker, mock_ffprobe_box):
    """Verify the default run stays metadata-only."""
    # Given: A file that probes cleanly
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        return_value=mock_ffprobe_box("reference.json"),
    )
    decode = mocker.patch("vid_cleaner.controllers.validation.decode_check")

    # When: Checking it without `--deep`
    args = ["check", str(mock_video_path)]
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: No decode was attempted
    decode.assert_not_called()


def test_check_from_directory_reports_unreadable_files(capsys, tmp_path, mocker, mock_ffprobe_box):
    """Verify `--from` checks every video file it discovers."""
    # Given: A directory holding one good file and one that cannot be probed
    library = tmp_path / "library"
    library.mkdir()
    good = library / "good.mkv"
    good.write_bytes(b"\0" * 100)
    broken = library / "broken.mkv"
    broken.write_bytes(b"\0" * 100)

    good_box = mock_ffprobe_box("reference.json")
    good_box.path_to_file = good

    def probe(path):
        if path.name == broken.name:
            raise VideoProbeError(path=path, reason="Invalid data found")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Checking the directory
    args = ["check", "--from", str(library)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: Both files are accounted for and the run fails
    assert exc_info.value.code == 1
    assert "Invalid data found" in captured.err
    assert "1 valid, 1 invalid" in captured.out


def test_check_from_reports_unreadable_files_despite_a_filter(
    capsys, tmp_path, mocker, mock_ffprobe_box
):
    """Verify a trait filter cannot hide a file that failed to read.

    An unreadable file carries no traits, so letting a filter exclude it would let
    `check` exit zero over the very files it exists to find.
    """
    # Given: A directory with one unreadable file and one readable file lacking the trait
    library = tmp_path / "library"
    library.mkdir()
    good = library / "good.mkv"
    good.write_bytes(b"\0" * 100)
    broken = library / "broken.mkv"
    broken.write_bytes(b"\0" * 100)

    good_box = mock_ffprobe_box("reference.json")
    good_box.path_to_file = good

    def probe(path):
        if path.name == broken.name:
            raise VideoProbeError(path=path, reason="Invalid data found")
        return good_box

    mocker.patch("vid_cleaner.models.video_file.get_probe_as_box", side_effect=probe)

    # When: Checking with a filter the readable file does not match
    args = ["check", "--from", str(library), "--filters", "vp9"]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The unreadable file is still reported and the run fails
    assert exc_info.value.code == 1
    assert "Invalid data found" in captured.err
    assert "0 valid, 1 invalid" in captured.out


def test_check_from_empty_directory_exits_zero(capsys, tmp_path):
    """Verify a directory holding no video files is not a failure."""
    # Given: An empty directory
    library = tmp_path / "library"
    library.mkdir()

    # When: Checking it
    args = ["check", "--from", str(library)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The run reports nothing to do and succeeds
    assert exc_info.value.code == 0
    assert "No video files found" in captured.out + captured.err


def test_check_from_nonexistent_directory_errors(capsys, tmp_path):
    """Verify a mistyped `--from` fails loudly instead of reporting nothing to check."""
    # Given: A path that is not a directory
    missing = tmp_path / "not_here"

    # When: Checking it
    args = ["check", "--from", str(missing)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The bad path is named
    assert exc_info.value.code == 1
    assert "must be an existing directory" in captured.err


def test_check_rejects_from_combined_with_files(capsys, tmp_path, mock_video_path):
    """Verify the explicit and discovery modes cannot be mixed."""
    # Given: Both a file path and a `--from` directory
    args = ["check", str(mock_video_path), "--from", str(tmp_path)]

    # When: Checking
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The conflict is reported
    assert exc_info.value.code == 1
    assert "cannot be combined with explicit file paths" in captured.err


def test_check_rejects_discovery_flags_without_from(capsys, mock_video_path):
    """Verify discovery flags without `--from` are an error rather than a silent no-op."""
    # Given: A filter applied to explicitly named files
    args = ["check", "--filters", "h264", str(mock_video_path)]

    # When: Checking
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The requirement is reported
    assert exc_info.value.code == 1
    assert "require `--from`" in captured.err


def test_check_with_no_arguments_errors(capsys):
    """Verify checking nothing is an error rather than a vacuous success."""
    # Given: No files and no `--from`
    args = ["check"]

    # When: Checking
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: The user is told what to provide
    assert exc_info.value.code == 1
    assert "Provide file path(s) or `--from`" in captured.err


def test_check_does_not_accept_limit(capsys, mock_video_path):
    """Verify `--limit` is absent, so a truncated selection cannot mask unchecked files."""
    # Given: A limit applied to a check run
    args = ["check", "--from", ".", "--limit", "5"]

    # When: Checking
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: The flag is rejected at parse time
    assert exc_info.value.code == 2


def test_check_prints_square_brackets_verbatim(capsys, tmp_path, mocker):
    """Verify bracketed filenames and ffmpeg reasons survive rendering intact.

    Release names and ffmpeg diagnostics both carry square brackets, which Rich reads as
    markup tags and drops, leaving a printed filename that does not exist on disk.
    """
    # Given: A bracketed filename that fails with a bracketed ffprobe reason
    movie = tmp_path / "Movie [x265].mkv"
    movie.touch()
    mocker.patch(
        "vid_cleaner.models.video_file.get_probe_as_box",
        side_effect=VideoProbeError(
            path=movie, reason="[matroska,webm @ 0x14f1] Read error at pos. 12345"
        ),
    )

    # When: Checking it
    args = ["check", str(movie)]
    with pytest.raises(cappa.Exit) as exc_info:
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    captured = capsys.readouterr()

    # Then: Both the name and the reason are printed as written
    assert exc_info.value.code == 1
    assert "Movie [x265].mkv" in captured.err
    assert "[matroska,webm @ 0x14f1] Read error" in captured.err


def test_display_path_uses_bare_name_in_cwd(tmp_path, monkeypatch):
    """Verify a file in the working directory prints as a bare filename."""
    # Given: A file in the current working directory
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "movie.mkv"
    path.touch()

    # When: Rendering it
    # Then: Only the name is shown
    assert display_path(path) == "movie.mkv"


def test_display_path_is_relative_below_cwd(tmp_path, monkeypatch):
    """Verify a file in a subdirectory prints as a path relative to the working directory."""
    # Given: A file one level below the working directory
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "movies" / "movie.mkv"
    nested.parent.mkdir()
    nested.touch()

    # When: Rendering it
    # Then: The subdirectory is shown so the name is not ambiguous
    assert display_path(nested) == str(Path("movies") / "movie.mkv")


def test_display_path_is_absolute_outside_cwd(tmp_path, monkeypatch):
    """Verify a file outside the working directory prints as an absolute path."""
    # Given: A working directory that does not contain the file
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    outside = tmp_path / "movie.mkv"
    outside.touch()

    # When: Rendering it
    # Then: The full path is shown
    assert display_path(outside) == str(outside.resolve())
