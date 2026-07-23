from vid_cleaner.config import settings


def test_verbosity_defaults_to_zero():
    """Verify verbosity setting defaults to 0."""
    assert int(settings.get("verbosity", 0) or 0) == 0
