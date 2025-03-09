"""Filesystem utilities."""

import io
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from rich.filesize import decimal
from rich.markup import escape
from rich.progress import Progress
from rich.text import Text
from rich.tree import Tree

from vid_cleaner.constants import BUFFER_SIZE
from vid_cleaner.utils import errors

from .printer import pp


def _copyfileobj(
    src_bytes: io.BufferedReader,
    dest_bytes: io.BufferedWriter,
    callback: Callable,
    length: int,
) -> None:
    """Copy bytes from a source file to a destination file with progress tracking.

    Read bytes in chunks from the source file and write them to the destination file, invoking a callback after each chunk to track progress. The callback receives the total bytes copied so far to enable progress reporting.

    Args:
        src_bytes (io.BufferedReader): Source file to read bytes from that supports the buffer protocol
        dest_bytes (io.BufferedWriter): Destination file to write bytes to that supports the buffer protocol
        callback (Callable[[int], None]): Function called after each chunk with total bytes copied
        length (int): Size of each chunk to read/write at a time
    """
    copied = 0
    while True:
        buf = src_bytes.read(length)
        if not buf:
            break
        dest_bytes.write(buf)
        copied += len(buf)
        if callback is not None:
            callback(copied)


def copy_with_callback(
    src: Path,
    dest: Path,
    callback: Callable | None = None,
    buffer_size: int = BUFFER_SIZE,
) -> Path:
    """Copy a file with progress tracking support.

    Copy a file from source to destination path with optional progress callback support. Track copy progress by invoking the callback after each chunk is copied. Configure chunk size to control callback frequency.

    Args:
        src (Path): Source file path to copy from
        dest (Path): Destination path to copy to. If directory, copies source file into it with same name
        callback (Callable | None, optional): Function to call after each chunk with bytes copied. Defaults to None.
        buffer_size (int, optional): Size of chunks to copy in bytes. Defaults to BUFFER_SIZE.

    Returns:
        Path: Path to the copied destination file

    Raises:
        FileNotFoundError: If source file does not exist
        SameFileError: If source and destination paths are the same file
        ValueError: If callback is provided but not callable
    """
    if not src.is_file():
        msg = f"src file `{src}` doesn't exist"
        raise FileNotFoundError(msg)

    dest = dest / src.name if dest.is_dir() else dest

    if dest.exists() and src.samefile(dest):
        msg = f"source file `{src}` and destination file `{dest}` are the same file."
        raise errors.SameFileError(msg)

    if callback is not None and not callable(callback):
        msg = f"callback must be callable, not {type(callback)}"  # type: ignore [unreachable]
        raise ValueError(msg)

    with src.open("rb") as src_bytes, dest.open("wb") as dest_bytes:
        _copyfileobj(src_bytes, dest_bytes, callback=callback, length=buffer_size)

    shutil.copymode(str(src), str(dest))

    return dest


def unique_filename(path: Path, separator: str = "_") -> Path:
    """Generate a unique filename by appending an incrementing number if the file already exists.

    Append an incrementing integer to the filename stem until finding a unique name that doesn't exist in the target directory. Preserve the original file extension.

    Args:
        path (Path): The file path to make unique
        separator (str): The string to use between filename and number. Defaults to "_".

    Returns:
        Path: A unique file path that does not exist in the target directory
    """
    if not path.exists():
        return path

    original_stem = path.stem
    i = 1
    while path.exists():
        path = path.with_name(f"{original_stem}{separator}{i}{path.suffix}")
        i += 1

    return path


def tmp_to_output(
    tmp_file: Path,
    stem: str,
    new_file: Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Copy a temporary file to an output location with optional renaming and overwrite control.

    Copy a temporary file to a specified output location, using either a provided output path or constructing one from the current directory and stem. Handle naming conflicts by appending numbers to the stem when overwrite is disabled. Display a progress bar during the copy operation.

    Args:
        tmp_file (Path): The path to the temporary input file to be copied
        stem (str): The base name (stem) to use for the output file if new_file is not provided
        overwrite (bool, optional): Whether to overwrite existing output files. Defaults to False.
        new_file (Path | None, optional): Optional explicit output file path. Defaults to None.

    Returns:
        Path: The path where the temporary file was copied
    """
    if new_file:
        parent = new_file.parent.expanduser().resolve()
        stem = new_file.stem
    else:
        parent = Path.cwd()

    # Ensure parent directory exists
    parent.mkdir(parents=True, exist_ok=True)

    new_stem = re.sub(r"_\d+\.[\w\d]{2,4}$", "", stem)
    new = parent / f"{new_stem}{tmp_file.suffix}"

    if not overwrite:
        new_stem = re.sub(r"_\d+$", "", stem)
        new = parent / f"{new_stem}{tmp_file.suffix}"
        new = unique_filename(new)
    else:
        new = parent / f"{stem}{tmp_file.suffix}"

    tmp_file_size = tmp_file.stat().st_size

    with Progress(transient=True) as progress:
        task = progress.add_task("Copy file…", total=tmp_file_size)
        copy_with_callback(
            tmp_file,
            new,
            callback=lambda total_copied: progress.update(task, completed=total_copied),
        )

    pp.trace(f"File copied to {new}")
    return new


def directory_tree(directory: Path, *, show_hidden: bool = False) -> Tree:
    """Build a tree representation of a directory's contents.

    Create a visual tree structure showing files and subdirectories within the given directory. Files are displayed with size and icons, directories are shown with folder icons.

    Copied from https://github.com/Textualize/rich/blob/master/examples/tree.py

    Args:
        directory (Path): The root directory to build the tree from
        show_hidden (bool, optional): Whether to include hidden files and directories in the tree. Defaults to False.

    Returns:
        Tree: A rich Tree object containing the directory structure
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
