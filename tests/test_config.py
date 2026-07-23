# type: ignore
"""Test the vidcleaner settings configuration."""

from vid_cleaner.config import settings


def test_verbosity_defaults_to_zero():
    """Verify verbosity setting defaults to 0."""
    assert settings.verbosity == 0
