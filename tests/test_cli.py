"""Test the vidcleaner command line interface."""

import cappa
import pytest

from vid_cleaner.vidcleaner import VidCleaner


@pytest.mark.parametrize(
    ("subcommand"),
    [("inspect"), ("clip"), ("clean"), ("cache")],
)
def test_vidcleaner_cli_help(clean_stdout, subcommand: str) -> None:
    """Verify help text displays usage information for each command."""
    # Given: Command line arguments for help
    args = [subcommand, "--help"] if subcommand else ["--help"]

    # When: Invoking the CLI with help flag
    with pytest.raises(cappa.Exit):
        cappa.invoke(obj=VidCleaner, argv=args)

    # Then: Help output contains expected usage information
    output = clean_stdout()
    assert "Usage: vidcleaner" in output
    assert "--help" in output
    assert " [-v]" in output
