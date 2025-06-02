"""Cache subcommand."""

import shutil

import cappa
from nclutils import console, directory_tree, pp

from vid_cleaner import settings
from vid_cleaner.vidcleaner import CacheCommand


def main(cache_cmd: CacheCommand) -> None:
    """Manage the cache directory for vidcleaner.

    Clear or display the contents of the cache directory used by vidcleaner. When displaying contents, show a tree view of all files including hidden ones.

    Args:
        cmd (VidCleaner): The main command instance containing global options
        cache_cmd (CacheCommand): The cache subcommand instance with cache-specific options

    Raises:
        cappa.Exit: If the cache directory is not found or if the cache is cleared
    """
    if not settings.CACHE_DIR.exists():
        pp.info(f"Cache directory not found: `{settings.CACHE_DIR}`")
        raise cappa.Exit(code=0)

    if cache_cmd.clear:
        shutil.rmtree(settings.CACHE_DIR)
        settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pp.success(f"Cache cleared: `{settings.CACHE_DIR}`")
        raise cappa.Exit(code=0)

    if len(list(settings.CACHE_DIR.iterdir())) > 0:
        tree = directory_tree(settings.CACHE_DIR, show_hidden=True)
        console.print(tree)
    else:
        pp.info(f"Cache directory is empty: `{settings.CACHE_DIR}`")

    raise cappa.Exit(code=0)
