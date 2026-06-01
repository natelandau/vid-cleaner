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
