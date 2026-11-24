"""Cache subcommand."""

import shutil
from typing import Annotated

import cappa
from cappa import Dep
from nclutils import pp
from nclutils.fs import directory_tree

from vid_cleaner.config import Config
from vid_cleaner.vidcleaner import CacheCommand, build_config


def main(cache_cmd: CacheCommand, config: Annotated[Config, Dep(build_config)]) -> None:
    """Manage the cache directory for vidcleaner.

    Clear or display the contents of the cache directory used by vidcleaner. When displaying contents, show a tree view of all files including hidden ones.

    Args:
        cache_cmd (CacheCommand): The cache subcommand instance with cache-specific options
        config (Config): The settings for this run, which name the cache directory

    Raises:
        cappa.Exit: If the cache directory is not found or if the cache is cleared
    """
    cache_dir = config.cache_dir
    if not cache_dir.exists():
        pp.info(f"Cache directory not found: `{cache_dir}`")
        raise cappa.Exit(code=0)

    if cache_cmd.clear:
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        pp.success(f"Cache cleared: `{cache_dir}`")
        raise cappa.Exit(code=0)

    if any(cache_dir.iterdir()):
        tree = directory_tree(cache_dir, show_hidden=True)
        pp.console().print(tree)
    else:
        pp.info(f"Cache directory is empty: `{cache_dir}`")

    raise cappa.Exit(code=0)
