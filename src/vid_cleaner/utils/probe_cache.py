"""Keep ffprobe results between runs so an unchanged library is not probed again."""

import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from nclutils import pp

from vid_cleaner.utils.interlace_detection import FieldOrder, InterlaceScan

PROBE_CACHE_FILENAME = "ffprobe-cache.sqlite3"
PROBE_CACHE_UNUSABLE_WARNING = "Probe cache is unusable"
# Entries for files that were deleted or renamed are never looked up again
UNUSED_ENTRY_MAX_AGE_SECONDS = 90 * 24 * 60 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    probe BLOB NOT NULL,
    used_at INTEGER NOT NULL
)
"""
_SCANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS interlace_scans (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    interlaced INTEGER,
    field_order TEXT,
    used_at INTEGER NOT NULL
)
"""


class ProbeCache:
    """Store raw ffprobe output and interlace verdicts keyed on a file's path, size, and modification time.

    Use one instance per run as a context manager, which closes the database, and share
    it across threads. A change to a file's size or modification time makes its entry a
    miss, and storing the new result replaces it, so the cache holds at most one entry
    per path and kind, and closing the cache drops entries unused for 90 days. Any database
    error turns the cache off for the rest of the run instead of failing it, since every
    lookup can fall back to running ffprobe or the scan.

    Args:
        directory (Path | None): The cache directory. The database file is created inside
            it on first use. None makes every lookup a miss and stores nothing.
    """

    def __init__(self, directory: Path | None) -> None:
        """Remember where the database lives without touching the filesystem."""
        self.database_path = directory / PROBE_CACHE_FILENAME if directory else None
        self._connection: sqlite3.Connection | None = None
        self._disabled = False
        self._lock = threading.Lock()
        self._paths_read: list[str] = []
        self._scan_paths_read: list[str] = []

    def __enter__(self) -> Self:
        """Return the cache for use in a `with` block.

        Returns:
            Self: This cache.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the database when the `with` block ends."""
        self.close()

    def close(self) -> None:
        """Record which entries this run used, drop long-unused ones, and turn the cache off.

        Probes still running after an interrupt finish after the `with` block ends; a
        closed cache makes their lookups miss instead of reopening a connection that
        nothing would close.
        """
        with self._lock:
            if self._connection is not None:
                self._record_use_and_prune(self._connection)
            self._disabled = True
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def get(self, path: Path, *, size: int, mtime_ns: int) -> dict[str, Any] | None:
        """Look up the stored ffprobe output for a file that has not changed since it was stored.

        Args:
            path (Path): The probed file.
            size (int): The file's current size in bytes.
            mtime_ns (int): The file's current modification time in nanoseconds.

        Returns:
            dict[str, Any] | None: The stored ffprobe output, or None on a miss.
        """
        with self._lock:
            connection = self._open()
            if connection is None:
                return None
            try:
                row = connection.execute(
                    "SELECT probe FROM probes WHERE path = ? AND size = ? AND mtime_ns = ?",
                    (str(path), size, mtime_ns),
                ).fetchone()
            except sqlite3.Error as e:
                self._disable(e)
                return None
            if row is not None:
                # Written in one batch at close, so a hit costs no commit
                self._paths_read.append(str(path))

        if row is None:
            return None
        try:
            probe = json.loads(zlib.decompress(row[0]))
        except (zlib.error, ValueError) as e:
            pp.debug(f"Ignoring unreadable probe cache entry for {path}: {e}")
            return None
        return probe if isinstance(probe, dict) else None

    def put(self, path: Path, *, size: int, mtime_ns: int, probe: dict[str, Any]) -> None:
        """Store a file's ffprobe output, replacing any older entry for the same path.

        Args:
            path (Path): The probed file.
            size (int): The file's size in bytes when it was probed.
            mtime_ns (int): The file's modification time in nanoseconds when it was probed.
            probe (dict[str, Any]): The raw ffprobe output.
        """
        # ffprobe JSON is repetitive text, so compression keeps a large library's cache small
        blob = zlib.compress(json.dumps(probe).encode())
        with self._lock:
            connection = self._open()
            if connection is None:
                return
            try:
                with connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO probes (path, size, mtime_ns, probe, used_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (str(path), size, mtime_ns, blob, int(time.time())),
                    )
            except sqlite3.Error as e:
                self._disable(e)

    def get_scan(self, path: Path, *, size: int, mtime_ns: int) -> InterlaceScan | None:
        """Look up the stored interlace verdict for a file that has not changed since it was scanned.

        Call it before scanning so an unchanged file is not decoded again.

        Args:
            path (Path): The scanned file.
            size (int): The file's current size in bytes.
            mtime_ns (int): The file's current modification time in nanoseconds.

        Returns:
            InterlaceScan | None: The stored verdict, which may be undecided, or None on a miss.
        """
        with self._lock:
            connection = self._open()
            if connection is None:
                return None
            try:
                row = connection.execute(
                    "SELECT interlaced, field_order FROM interlace_scans "
                    "WHERE path = ? AND size = ? AND mtime_ns = ?",
                    (str(path), size, mtime_ns),
                ).fetchone()
            except sqlite3.Error as e:
                self._disable(e)
                return None
            if row is None:
                return None
            self._scan_paths_read.append(str(path))

        interlaced, field_order = row
        try:
            return InterlaceScan(
                interlaced=None if interlaced is None else bool(interlaced),
                field_order=None if field_order is None else FieldOrder(field_order),
            )
        except ValueError as e:
            pp.debug(f"Ignoring unreadable interlace cache entry for {path}: {e}")
            return None

    def put_scan(self, path: Path, *, size: int, mtime_ns: int, scan: InterlaceScan) -> None:
        """Store a file's interlace verdict, replacing any older entry for the same path.

        Args:
            path (Path): The scanned file.
            size (int): The file's size in bytes when it was scanned.
            mtime_ns (int): The file's modification time in nanoseconds when it was scanned.
            scan (InterlaceScan): The verdict, including an undecided one.
        """
        with self._lock:
            connection = self._open()
            if connection is None:
                return
            try:
                with connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO interlace_scans "
                        "(path, size, mtime_ns, interlaced, field_order, used_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(path),
                            size,
                            mtime_ns,
                            None if scan.interlaced is None else int(scan.interlaced),
                            scan.field_order.value if scan.field_order else None,
                            int(time.time()),
                        ),
                    )
            except sqlite3.Error as e:
                self._disable(e)

    def _open(self) -> sqlite3.Connection | None:
        """Open the database on first use. Call only while holding the lock.

        Returns:
            sqlite3.Connection | None: The connection, or None when the cache is off.
        """
        if self._disabled or self.database_path is None:
            return None
        if self._connection is not None:
            return self._connection
        connection: sqlite3.Connection | None = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            # Worker threads share this connection; the lock serializes every use of it
            connection = sqlite3.connect(self.database_path, check_same_thread=False)
            # Every probed file is its own commit; WAL without a sync per commit keeps those
            # cheap, and a cache can afford to lose its last writes in a power cut
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(_SCHEMA)
            connection.execute(_SCANS_SCHEMA)
        except (OSError, sqlite3.Error) as e:
            if connection is not None:
                connection.close()
            self._disable(e)
            return None
        self._connection = connection
        return connection

    def _record_use_and_prune(self, connection: sqlite3.Connection) -> None:
        """Mark this run's cache hits as used, then drop entries unused for too long.

        Call only while holding the lock.

        Args:
            connection (sqlite3.Connection): The open database.
        """
        now = int(time.time())
        try:
            with connection:
                connection.executemany(
                    "UPDATE probes SET used_at = ? WHERE path = ?",
                    [(now, path) for path in self._paths_read],
                )
                connection.executemany(
                    "UPDATE interlace_scans SET used_at = ? WHERE path = ?",
                    [(now, path) for path in self._scan_paths_read],
                )
                for table in ("probes", "interlace_scans"):
                    connection.execute(
                        f"DELETE FROM {table} WHERE used_at < ?",  # noqa: S608
                        (now - UNUSED_ENTRY_MAX_AGE_SECONDS,),
                    )
        except sqlite3.Error as e:
            self._disable(e)
        self._paths_read.clear()
        self._scan_paths_read.clear()

    def _disable(self, error: Exception) -> None:
        """Turn the cache off for the rest of the run and say why. Call only while holding the lock.

        Args:
            error (Exception): The error that made the database unusable.
        """
        self._disabled = True
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        pp.warning(
            f"{PROBE_CACHE_UNUSABLE_WARNING}: {self.database_path} ({error}). Reading every "
            "file with ffprobe instead. If the file is damaged, run `vidcleaner cache --clear`."
        )
