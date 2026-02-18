"""
Agent status history tracking.

Provides functions to log and read agent status history for timeline visualization.
"""

import csv
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .settings import PATHS


def log_agent_status(
    agent_name: str,
    status: str,
    activity: str = "",
    history_file: Optional[Path] = None
) -> None:
    """Log agent status to history CSV file.

    Called by daemon each loop to track agent status over time.
    Used by TUI for timeline visualization.

    Args:
        agent_name: Name of the agent
        status: Current status string
        activity: Optional activity description
        history_file: Optional path override (for testing)
    """
    path = history_file or PATHS.agent_history
    path.parent.mkdir(parents=True, exist_ok=True)

    # Check if file exists (to write header)
    write_header = not path.exists()

    with open(path, 'a', newline='') as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(['timestamp', 'agent', 'status', 'activity'])
        writer.writerow([
            datetime.now().isoformat(),
            agent_name,
            status,
            activity[:100] if activity else ""
        ])


class StatusHistoryFile:
    """Cached incremental reader for agent_status_history.csv.

    Optimizations over naive full-file parsing:
    - Binary seek for initial read (skip old rows by byte offset)
    - Incremental tail reads (only parse newly appended bytes)
    - mtime+size cache (instant return when file unchanged)
    - Thread-safe (lock protects cache state)
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._cached_mtime: float = 0.0
        self._cached_size: int = 0
        self._cached_entries: List[Tuple[datetime, str, str, str]] = []
        self._cached_hours: float = 0.0
        self._read_offset: int = 0

    def read(
        self,
        hours: float = 3.0,
        agent_name: Optional[str] = None,
    ) -> List[Tuple[datetime, str, str, str]]:
        """Read status history entries, using cache when possible."""
        try:
            stat = self._path.stat()
        except OSError:
            return []

        with self._lock:
            file_changed = (
                stat.st_mtime != self._cached_mtime
                or stat.st_size != self._cached_size
            )
            hours_expanded = hours > self._cached_hours and self._cached_hours > 0

            # Cache hit: file unchanged and hours within cached window
            if not file_changed and not hours_expanded:
                return self._filter(self._cached_entries, hours, agent_name)

            # Incremental: file grew, hours didn't expand, have previous offset
            if (
                file_changed
                and not hours_expanded
                and stat.st_size > self._cached_size
                and self._read_offset > 0
            ):
                return self._incremental_read(stat, hours, agent_name)

            # Full re-read for all other cases
            return self._full_read(stat, hours, agent_name)

    def _full_read(self, stat, hours, agent_name):
        cutoff = datetime.now() - timedelta(hours=hours)
        try:
            with open(self._path, 'rb') as f:
                start = self._seek_to_cutoff(f, cutoff, stat.st_size)
                entries = self._parse_rows(f, start)
        except (OSError, IOError):
            return []

        self._cached_entries = entries
        self._cached_mtime = stat.st_mtime
        self._cached_size = stat.st_size
        self._cached_hours = hours
        self._read_offset = stat.st_size
        return self._filter(entries, hours, agent_name)

    def _incremental_read(self, stat, hours, agent_name):
        try:
            with open(self._path, 'rb') as f:
                new_entries = self._parse_rows(f, self._read_offset)
        except (OSError, IOError):
            new_entries = []

        # Trim entries that have aged out of the cached window
        cutoff = datetime.now() - timedelta(hours=self._cached_hours)
        self._cached_entries = [e for e in self._cached_entries if e[0] >= cutoff]
        self._cached_entries.extend(new_entries)
        self._cached_mtime = stat.st_mtime
        self._cached_size = stat.st_size
        self._read_offset = stat.st_size
        return self._filter(self._cached_entries, hours, agent_name)

    @staticmethod
    def _seek_to_cutoff(f, cutoff: datetime, file_size: int) -> int:
        """Binary search for byte offset where timestamps >= cutoff."""
        f.seek(0)
        f.readline()  # skip header
        data_start = f.tell()

        if data_start >= file_size:
            return data_start

        cutoff_bytes = cutoff.isoformat().encode('ascii')
        lo = data_start
        hi = file_size

        while lo < hi:
            mid = (lo + hi) // 2
            f.seek(mid)
            if mid > data_start:
                f.readline()  # align to next line start

            pos = f.tell()
            if pos >= hi:
                hi = mid
                continue

            line = f.readline()
            if not line or not line.strip():
                lo = pos + max(len(line), 1)
                continue

            comma = line.find(b',')
            if comma == -1:
                lo = f.tell()
                continue

            ts_bytes = line[:comma]
            if ts_bytes >= cutoff_bytes:
                hi = pos
            else:
                lo = f.tell()

        return lo

    @staticmethod
    def _parse_rows(f, start_offset: int) -> List[Tuple[datetime, str, str, str]]:
        """Parse CSV rows from start_offset to end of file."""
        f.seek(start_offset)
        data = f.read().decode('utf-8', errors='replace')
        entries: List[Tuple[datetime, str, str, str]] = []
        for row in csv.reader(data.splitlines()):
            if len(row) < 3:
                continue
            if row[0] == 'timestamp':
                continue
            try:
                ts = datetime.fromisoformat(row[0])
                entries.append((ts, row[1], row[2], row[3] if len(row) > 3 else ''))
            except (ValueError, IndexError):
                continue
        return entries

    @staticmethod
    def _filter(entries, hours, agent_name):
        cutoff = datetime.now() - timedelta(hours=hours)
        if agent_name is None:
            return [e for e in entries if e[0] >= cutoff]
        return [e for e in entries if e[0] >= cutoff and e[1] == agent_name]


# ── Module-level reader cache ────────────────────────────────────────

_readers: Dict[str, StatusHistoryFile] = {}
_readers_lock = threading.Lock()


def _get_or_create_reader(path: Path) -> StatusHistoryFile:
    key = str(path)
    with _readers_lock:
        reader = _readers.get(key)
        if reader is None:
            reader = StatusHistoryFile(path)
            _readers[key] = reader
        return reader


def read_agent_status_history(
    hours: float = 3.0,
    agent_name: Optional[str] = None,
    history_file: Optional[Path] = None
) -> List[Tuple[datetime, str, str, str]]:
    """Read agent status history from CSV file.

    Args:
        hours: How many hours of history to read (default 3)
        agent_name: Optional - filter to specific agent
        history_file: Optional path override (for testing)

    Returns:
        List of (timestamp, agent, status, activity) tuples, oldest first
    """
    path = history_file or PATHS.agent_history
    return _get_or_create_reader(path).read(hours, agent_name)


def get_agent_timeline(
    agent_name: str,
    hours: float = 3.0,
    history_file: Optional[Path] = None
) -> List[Tuple[datetime, str]]:
    """Get simplified timeline for a specific agent.

    Args:
        agent_name: Name of the agent
        hours: How many hours of history (default 3)
        history_file: Optional path override (for testing)

    Returns:
        List of (timestamp, status) tuples for the agent
    """
    history = read_agent_status_history(hours, agent_name, history_file)
    return [(ts, status) for ts, _, status, _ in history]


def clear_old_history(
    max_age_hours: float = 24.0,
    history_file: Optional[Path] = None
) -> int:
    """Remove old entries from history file.

    Args:
        max_age_hours: Remove entries older than this (default 24 hours)
        history_file: Optional path override (for testing)

    Returns:
        Number of entries removed
    """
    path = history_file or PATHS.agent_history

    if not path.exists():
        return 0

    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    kept_entries: List[List[str]] = []
    removed_count = 0

    try:
        with open(path, 'r', newline='') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header:
                kept_entries.append(header)

            for row in reader:
                try:
                    ts = datetime.fromisoformat(row[0])
                    if ts >= cutoff:
                        kept_entries.append(row)
                    else:
                        removed_count += 1
                except (ValueError, IndexError):
                    # Keep malformed entries
                    kept_entries.append(row)

        # Only rewrite if we removed entries
        if removed_count > 0:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(kept_entries)

    except (OSError, IOError):
        pass

    return removed_count
