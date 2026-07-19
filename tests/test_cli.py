"""Test the vidcleaner command line interface."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner.utils import copy_to_output
from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.mark.parametrize(
    ("subcommand"),
    [("inspect"), ("clip"), ("clean"), ("cache")],
)
def test_vidcleaner_cli_help(capsys, subcommand: str) -> None:
    """Verify help text displays for each subcommand."""
    # Given: Command line arguments requesting help
    args = [subcommand, "--help"] if subcommand else ["--help"]

    # When: Invoking CLI with help flag
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Help output contains expected information
    output = capsys.readouterr().out
    assert "Usage: vidcleaner" in output
    assert "--help" in output
    assert " [-v]" in output


def test_copy_to_output_backs_up_existing(tmp_path: Path) -> None:
    """Verify writing over an existing file backs it up and reports both actions."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying to the destination without overwrite
    out_file, messages = copy_to_output(src, dst, overwrite=False)

    # Then: the original is preserved as a backup and the new content is written
    backups = list(dst.resolve().parent.glob("movie.mkv.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original content"
    assert out_file.read_text() == "new content"

    # And: both the backup and the save are reported
    assert any("Backed up original to" in m for m in messages)
    assert any("Saved to" in m and "movie.mkv" in m for m in messages)


def test_copy_to_output_overwrite_skips_backup(tmp_path: Path) -> None:
    """Verify overwrite mode replaces the destination without keeping a backup."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying with overwrite enabled
    out_file, messages = copy_to_output(src, dst, overwrite=True)

    # Then: no backup is created and only the save is reported
    assert list(dst.resolve().parent.glob("*.bak")) == []
    assert out_file.read_text() == "new content"
    assert all("Backed up" not in m for m in messages)
    assert any("Saved to" in m for m in messages)


def test_copy_to_output_preserves_original_when_staging_fails(tmp_path: Path, mocker) -> None:
    """Verify a staging failure leaves the original intact with no backup or temp file."""
    # Given: an existing destination and a source, with the staging copy set to fail
    #        after partially writing (mimicking a real mid-copy I/O error)
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    def _fail_mid_copy(*, src, dst, **kwargs):
        Path(dst).write_text("corrupt partial")
        raise OSError(5, "Input/output error")

    mocker.patch("vid_cleaner.utils.cli.copy_file", autospec=True, side_effect=_fail_mid_copy)

    # When: the staging copy fails
    with pytest.raises(OSError, match="Input/output error"):
        copy_to_output(src, dst, overwrite=True)

    # Then: the original is byte-for-byte intact and nothing is left behind
    assert dst.read_text() == "original content"
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob(".*vidcleaner-tmp*")) == []


@pytest.mark.parametrize("overwrite", [True, False])
def test_copy_to_output_leaves_no_temp_file_on_success(tmp_path: Path, *, overwrite: bool) -> None:
    """Verify a successful write leaves no staging temp file behind in either mode."""
    # Given: a processed source and an existing destination
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")

    # When: copying to the destination
    copy_to_output(src, dst, overwrite=overwrite)

    # Then: the content is written and no staging temp remains
    assert dst.read_text() == "new content"
    assert list(tmp_path.glob(".*vidcleaner-tmp*")) == []


@pytest.mark.parametrize("overwrite", [True, False])
def test_copy_to_output_creates_new_destination(tmp_path: Path, *, overwrite: bool) -> None:
    """Verify copying to a non-existent destination creates it in both modes."""
    # Given: a processed source and a destination that does not exist yet
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"

    # When: copying to the missing destination
    out_file, messages = copy_to_output(src, dst, overwrite=overwrite)

    # Then: the destination is created with no backup and the save is reported
    assert out_file.read_text() == "new content"
    assert list(tmp_path.glob("*.bak")) == []
    assert any("Saved to" in m for m in messages)


def test_copy_to_output_overwrite_does_not_mutate_hardlink(tmp_path: Path) -> None:
    """Verify overwriting replaces the inode so existing hardlinks keep the original content."""
    # Given: a destination with a hardlink sharing its inode (as *arr apps / seeding create)
    src = tmp_path / "processed.mkv"
    src.write_text("new content")
    dst = tmp_path / "movie.mkv"
    dst.write_text("original content")
    link = tmp_path / "seed.mkv"
    link.hardlink_to(dst)

    # When: overwriting the destination
    copy_to_output(src, dst, overwrite=True)

    # Then: the destination has the new content but the hardlink keeps the original
    assert dst.read_text() == "new content"
    assert link.read_text() == "original content"
