"""Controllers for the VidCleaner application."""

# `discovery` is deliberately absent: it imports `vid_cleaner.models`, which imports this
# package, so re-exporting it here to match `temp_files` makes both unimportable. Import it
# as `vid_cleaner.controllers.discovery` instead.
from .temp_files import TempFile

__all__ = ["TempFile"]
