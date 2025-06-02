"""Test the vidcleaner command line interface."""

import cappa
import pytest

from vid_cleaner.vidcleaner import VidCleaner, config_subcommand


@pytest.mark.parametrize(
    ("subcommand"),
    [("inspect"), ("clip"), ("clean"), ("cache")],
)
def test_vidcleaner_cli_help(clean_stdout, subcommand: str) -> None:
    """Verify help text displays for each subcommand."""
    # Given: Command line arguments requesting help
    args = [subcommand, "--help"] if subcommand else ["--help"]

    # When: Invoking CLI with help flag
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])

    # Then: Help output contains expected information
    output = clean_stdout()
    assert "Usage: vidcleaner" in output
    assert "--help" in output
    assert " [-v]" in output
