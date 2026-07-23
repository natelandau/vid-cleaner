# type: ignore
"""Test the vidcleaner settings configuration."""

from vid_cleaner.config import SettingsManager


def test_verbosity_defaults_to_zero():
    """Verify the verbosity validator supplies a default of 0."""
    # Given the settings singleton is re-initialized from scratch
    original = SettingsManager._instance  # noqa: SLF001
    try:
        SettingsManager._instance = None  # noqa: SLF001
        fresh = SettingsManager.initialize()

        # Then the validator-supplied default is 0
        assert fresh.verbosity == 0
    finally:
        SettingsManager._instance = original  # noqa: SLF001
