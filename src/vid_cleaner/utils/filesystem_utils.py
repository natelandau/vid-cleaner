"""Filesystem utilities."""

import re
import shutil
from pathlib import Path

from rich.filesize import decimal
from rich.markup import escape
from rich.progress import Progress, TaskID
from rich.text import Text
from rich.tree import Tree

from vid_cleaner.constants import IO_BUFFER_SIZE

from .printer import pp


def unique_filename(path: Path, separator: str = "_", *, continue_sequence: bool = False) -> Path:
    """Create a unique filename by incrementing a number suffix until finding an unused name.

    Modify the filename stem by appending an incrementing number separated by the given separator character. When continue_sequence is True, strip any existing number suffix before incrementing. Maintain the original file extension.

    Args:
        path (Path): Path to make unique by adding a number suffix
        separator (str): Character(s) to separate filename from number suffix. Defaults to "_"
        continue_sequence (bool): Strip any existing number suffix before incrementing, effectively continuing and existing sequence. Defaults to False

    Returns:
        Path: A unique path that does not exist in the target directory

    Changelog:
        - v2.2.1: Initial version
        - v2.3: Added has_separator parameter
    """
    if not path.exists():
        return path

    if continue_sequence:
        original_stem = re.sub(rf"{re.escape(separator)}\d+", "", path.stem)
    else:
        original_stem = path.stem

    i = 1
    while path.exists():
        path = path.with_name(f"{original_stem}{separator}{i}{path.suffix}")
        i += 1

    return path


def directory_tree(directory: Path, *, show_hidden: bool = False) -> Tree:
    """Build a tree representation of a directory's contents.

    Create a visual tree structure showing files and subdirectories within the given directory. Files are displayed with size and icons, directories are shown with folder icons.

    Copied from https://github.com/Textualize/rich/blob/master/examples/tree.py

    Args:
        directory (Path): The root directory to build the tree from
        show_hidden (bool, optional): Whether to include hidden files and directories in the tree. Defaults to False.

    Returns:
        Tree: A rich Tree object containing the directory structure

    Changelog:
        - Script utils: v2.2.1
    """

    def _walk_directory(directory: Path, tree: Tree, *, show_hidden: bool = False) -> None:
        """Recursively build a Tree with directory contents."""
        # Sort dirs first then by filename
        paths = sorted(
            Path(directory).iterdir(),
            key=lambda path: (path.is_file(), path.name.lower()),
        )
        for path in paths:
            if not show_hidden and path.name.startswith("."):
                continue

            if path.is_dir():
                style = "dim" if path.name.startswith("__") or path.name.startswith(".") else ""
                branch = tree.add(
                    f"[bold magenta]:open_file_folder: [link file://{path}]{escape(path.name)}",
                    style=style,
                    guide_style=style,
                )
                _walk_directory(path, branch, show_hidden=show_hidden)
            else:
                text_filename = Text(path.name, "green")
                text_filename.highlight_regex(r"\..*$", "bold red")
                text_filename.stylize(f"link file://{path}")
                file_size = path.stat().st_size
                text_filename.append(f" ({decimal(file_size)})", "blue")
                icon = "📄 "
                tree.add(Text(icon) + text_filename)

    tree = Tree(
        f":open_file_folder: [link file://{directory}]{directory}",
        guide_style="bright_blue",
    )
    _walk_directory(Path(directory), tree, show_hidden=show_hidden)
    return tree


def copy_file(
    src: Path,
    dst: Path,
    *,
    with_progress: bool = False,
    transient: bool = True,
    overwrite: bool = False,
) -> Path:
    """Copy a file to a destination with optional progress tracking.

    Copy files with granular control over progress display and file conflict handling. Preserve original file permissions while providing visual feedback for long-running operations.

    Args:
        src (Path): Source file to copy
        dst (Path): Destination path for the copy
        with_progress (bool, optional): Show a progress bar during copy. Defaults to False
        transient (bool, optional): Remove the progress bar after completion. Defaults to True
        overwrite (bool, optional): Overwrite existing destination files. If False, generate a unique filename. Defaults to False

    Returns:
        Path: Path to the destination file after copy completion

    Changelog:
        - v2.3: Initial version

    Raises:
        FileNotFoundError: If source file does not exist or is not a regular file
    """

    def _do_copy(
        src: Path, dst: Path, *, progress_bar: Progress | None = None, task: TaskID | None = None
    ) -> None:
        """Copy file contents in chunks with optional progress tracking.

        Args:
            src (Path): Source file to read from
            dst (Path): Destination file to write to
            progress_bar (Progress | None, optional): Progress bar instance for tracking. Defaults to None
            task (TaskID | None, optional): Task ID for progress updates. Defaults to None
        """
        with src.open("rb") as src_bytes, dst.open("wb") as dst_bytes:
            total_bytes_copied = 0
            while True:
                buf = src_bytes.read(IO_BUFFER_SIZE)
                if not buf:
                    break
                dst_bytes.write(buf)
                total_bytes_copied += len(buf)
                if progress_bar is not None and task is not None:
                    progress_bar.update(task, completed=total_bytes_copied)

    if not src.exists():
        msg = f"source file `{src}` does not exist. Did not copy."
        raise FileNotFoundError(msg)

    if not src.is_file():
        msg = f"source file `{src}` is not a file. Did not copy."
        raise FileNotFoundError(msg)

    dst = dst.parent.expanduser().resolve() / dst.name

    # Check if source and destination are the same to avoid unnecessary copy
    if src == dst or (dst.exists() and src.samefile(dst)):
        msg = f"source file `{src}` and destination file `{dst}` are the same file. Did not copy."
        pp.warning(msg)
        return src

    # Generate unique filename if destination exists and overwrite is disabled
    if dst.exists() and not overwrite:
        dst = unique_filename(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Copy file in chunks with progress bar to handle large files efficiently
    if with_progress:
        with Progress(transient=transient) as progress_bar:
            task = progress_bar.add_task(f"Copy {src.name}… ", total=src.stat().st_size)
            _do_copy(src, dst, progress_bar=progress_bar, task=task)
    else:
        _do_copy(src, dst)

    # Preserve original file permissions
    shutil.copymode(str(src), str(dst))

    return dst
