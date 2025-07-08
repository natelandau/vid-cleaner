"""Manage temporary files."""

import uuid
from pathlib import Path

from nclutils import pp

from vid_cleaner import settings


class TempFile:
    """Manage temporary files created during video processing.

    This class handles the creation, tracking, and cleanup of temporary files generated during video processing operations. It maintains a unique temporary directory for each instance and manages file naming and cleanup.

    Args:
        path (Path): The path to the original video file.

    Attributes:
        path (Path): The resolved path to the original video file
        suffix (str): The file extension of the original video file
        tmp_dir (Path): Path to the unique temporary directory for this instance
        created_tmp_files (list[Path]): List tracking all temporary files created
        tmp_file_number (int): Counter for naming temporary files
    """

    def __init__(self, path: Path) -> None:
        """Initialize the TempFile controller.

        Args:
            path (Path): Path to the original video file to process
        """
        self.path = path.expanduser().resolve()
        self.suffix = path.suffix
        self.tmp_dir = settings.CACHE_DIR / uuid.uuid4().hex
        self.created_tmp_files: list[Path] = []
        self.tmp_file_number = 1

    def latest_temp_path(self) -> Path:
        """Get the path to the most recently created temporary file.

        Returns the path to the most recent temporary file that was created. If no
        temporary files exist yet, returns the path to the original input file.

        Returns:
            Path: Path to the most recent temporary file or original file
        """
        return self.created_tmp_files[-1] if self.created_tmp_files else self.path

    def new_tmp_path(self, suffix: str = "", step_name: str = "") -> Path:
        """Generate a new tmp path for the creation of a new temporary file.

        Creates a new unique path for a temporary file in the temp directory. The path
        will include an incrementing number and optional step name in the filename.

        Args:
            suffix (str | None, optional): File extension to use. Defaults to original file's suffix.
            step_name (str | None, optional): Name of processing step to include in filename. Defaults to None.

        Returns:
            Path: Path object for the new temporary file
        """
        # Get the output file name
        suffix = suffix or self.suffix
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        if not step_name:
            step_name = "no_step"

        # Create a new tmp file name
        for file in self.tmp_dir.iterdir():
            if file.stem.startswith(f"{self.tmp_file_number}_"):
                self.tmp_file_number += 1
        pp.trace(f"Create new tmp file: {self.tmp_file_number}_{step_name}{suffix}")
        return self.tmp_dir / f"{self.tmp_file_number}_{step_name}{suffix}"

    def created_temp_file(self, path: Path) -> None:
        """Register a newly created temporary file.

        Adds the given path to the list of created temporary files and triggers cleanup
        of older temporary files to manage disk space.

        Args:
            path (Path): Path to the newly created temporary file
        """
        self.created_tmp_files.append(path)
        # Always clean old temporary files to minimize the size of the tmp directory
        self.clean_old_tmp_files()

    def clean_old_tmp_files(self) -> None:
        """Remove older temporary files.

        Deletes all temporary files except the most recent one to conserve disk space. Files are identified as "old" if their number is less than the current counter.
        """
        if self.tmp_dir.exists():
            for file in self.tmp_dir.iterdir():
                tmp_file_number = int(file.stem.split("_")[0])
                if tmp_file_number < self.tmp_file_number:
                    pp.trace(f"Remove tmp file: {file}")
                    file.unlink()

    def clean_up(self) -> None:
        """Remove all temporary files and the temporary directory.

        Performs complete cleanup by removing all temporary files created during
        processing and the temporary directory itself.
        """
        if self.tmp_dir.exists():
            pp.debug("Clean up temporary files")

            for file in self.tmp_dir.iterdir():
                pp.trace(f"Remove tmp file: {file}")
                file.unlink()

            pp.trace(f"Remove: {self.tmp_dir}")
            self.tmp_dir.rmdir()
