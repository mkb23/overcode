"""
Agent status history tracking.

Provides functions to log and read agent status history for timeline visualization.
"""

import csv
import gzip
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .settings import PATHS


def log_agent_status(
    agent_name: str,
    status: str,
    activity: str = "",
    history_file: Optional[Path] = None,
    session_id: str = "",
    hostname: str = "",
) -> None:
    """Log agent status to history CSV file.

    Called by daemon each loop to track agent status over time.
    Used by TUI for timeline visualization.

    Args:
        agent_name: Name of the agent
        status: Current status string
        activity: Optional activity description
        history_file: Optional path override (for testing)
        session_id: Unique session ID (UUID) for disambiguation
        hostname: Machine hostname for multi-host disambiguation
    """
    path = history_file or PATHS.agent_history
    path.parent.mkdir(parents=True, exist_ok=True)

    # Check if file exists (to write header)
    write_header = not path.exists()

    with open(path, 'a', newline='') as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(['timestamp', 'agent', 'status', 'activity', 'session_id', 'hostname'])
        writer.writerow([
            datetime.now().isoformat(),
            agent_name,
            status,
            activity[:100] if activity else "",
            session_id,
            hostname,
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
        self._cached_entries: List[Tuple[datetime, str, str, str, str, str]] = []
        self._cached_hours: float = 0.0
        self._read_offset: int = 0

    def read(
        self,
        hours: float = 3.0,
        agent_name: Optional[str] = None,
    ) -> List[Tuple[datetime, str, str, str, str, str]]:
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
    def _parse_rows(f, start_offset: int) -> List[Tuple[datetime, str, str, str, str, str]]:
        """Parse CSV rows from start_offset to end of file."""
        f.seek(start_offset)
        data = f.read().decode('utf-8', errors='replace')
        entries: List[Tuple[datetime, str, str, str, str, str]] = []
        for row in csv.reader(data.splitlines()):
            if len(row) < 3:
                continue
            if row[0] == 'timestamp':
                continue
            try:
                ts = datetime.fromisoformat(row[0])
                entries.append((
                    ts,
                    row[1],                             # agent
                    row[2],                             # status
                    row[3] if len(row) > 3 else '',     # activity
                    row[4] if len(row) > 4 else '',     # session_id
                    row[5] if len(row) > 5 else '',     # hostname
                ))
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
) -> List[Tuple[datetime, str, str, str, str, str]]:
    """Read agent status history from CSV file.

    Args:
        hours: How many hours of history to read (default 3)
        agent_name: Optional - filter to specific agent
        history_file: Optional path override (for testing)

    Returns:
        List of (timestamp, agent, status, activity, session_id, hostname)
        tuples, oldest first. session_id and hostname may be empty for
        rows written before v0.3.6.
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
    return [(ts, status) for ts, _, status, _, _, _ in history]


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


# ── Rotation, compression, and retention (#465, #468) ───────────────────
#
# agent_status_history.csv grows without bound (one row per agent per
# monitor-daemon loop). The windowed readers above (StatusHistoryFile /
# read_agent_status_history) only ever read the active CSV and are used
# with small windows — 3h (TUI timeline default) and 24h (parquet export,
# data_export.py). ROTATION_KEEP_HOURS is set comfortably above the widest
# of those so a rotation can never remove data a windowed reader still
# needs, even with a slow (hourly) rotation check racing a read.

ARCHIVE_SUFFIX = ".csv.gz"
ARCHIVE_TS_FORMAT = "%Y%m%d-%H%M%S"

DEFAULT_ROTATE_MB = 50.0
DEFAULT_ROTATE_MAX_AGE_DAYS = 7.0
DEFAULT_RETENTION_DAYS = 540.0  # ~18 months: archives are compact, keep the full analytics horizon
ROTATION_KEEP_HOURS = 30.0  # margin above the 24h widest windowed reader

_HISTORY_HEADER = ['timestamp', 'agent', 'status', 'activity', 'session_id', 'hostname']


def _archive_glob(history_file: Path) -> List[Path]:
    """Rotated archives for history_file, oldest name first."""
    return sorted(history_file.parent.glob(f"{history_file.stem}.*{ARCHIVE_SUFFIX}"))


def _parse_archive_timestamp(archive: Path, history_file: Path) -> Optional[datetime]:
    """Parse the rotation timestamp out of an archive's filename, if it matches."""
    prefix = history_file.stem + "."
    name = archive.name
    if not name.startswith(prefix) or not name.endswith(ARCHIVE_SUFFIX):
        return None
    middle = name[len(prefix):-len(ARCHIVE_SUFFIX)]
    try:
        return datetime.strptime(middle, ARCHIVE_TS_FORMAT)
    except ValueError:
        return None


def _oldest_row_timestamp(history_file: Path) -> Optional[datetime]:
    """Read just the first parseable data row's timestamp (no full scan)."""
    try:
        with open(history_file, 'r', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0] == 'timestamp':
                    continue
                try:
                    return datetime.fromisoformat(row[0])
                except (ValueError, IndexError):
                    continue
    except (OSError, IOError):
        return None
    return None


def rotate_status_history(
    history_file: Path,
    *,
    rotate_mb: float = DEFAULT_ROTATE_MB,
    max_age_days: float = DEFAULT_ROTATE_MAX_AGE_DAYS,
    keep_hours: float = ROTATION_KEEP_HOURS,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Rotate history_file to a compressed archive when it's too big or too old.

    Triggers when the active file exceeds rotate_mb or its oldest row is
    older than max_age_days. Rows older than `now - keep_hours` move to a
    new `<name>.<YYYYMMDD-HHMMSS>.csv.gz` archive alongside the active
    file; rows within keep_hours stay behind in a freshly-written active
    file, so windowed readers (3h/24h) are unaffected by rotation.

    Both writes use write-temp + os.replace, so a concurrent reader never
    observes a partially-written file — only the pre- or post-rotation
    file, whole (verified by test_status_history.py's shrink-tolerance
    coverage of the same StatusHistoryFile cache used here).

    Returns the archive path if a rotation happened, else None — including
    when nothing needs rotating, or the trigger was size but every row is
    within keep_hours (nothing safe to archive yet).
    """
    now = now or datetime.now()
    try:
        stat = history_file.stat()
    except OSError:
        return None

    oldest_ts = _oldest_row_timestamp(history_file)
    size_trigger = stat.st_size > rotate_mb * 1024 * 1024
    age_trigger = oldest_ts is not None and (now - oldest_ts) > timedelta(days=max_age_days)
    if not (size_trigger or age_trigger):
        return None

    cutoff = now - timedelta(hours=keep_hours)
    header = _HISTORY_HEADER
    keep_rows: List[List[str]] = []
    archive_rows: List[List[str]] = []

    try:
        with open(history_file, 'r', newline='') as f:
            reader = csv.reader(f)
            first = next(reader, None)
            if first and first[0] == 'timestamp':
                header = first
            elif first:
                keep_rows.append(first)  # no header — legacy/malformed, keep as-is
            for row in reader:
                if not row:
                    continue
                try:
                    ts = datetime.fromisoformat(row[0])
                except (ValueError, IndexError):
                    keep_rows.append(row)  # can't classify — never silently drop data
                    continue
                (archive_rows if ts < cutoff else keep_rows).append(row)
    except (OSError, IOError):
        return None

    if not archive_rows:
        return None

    archive_path = history_file.parent / (
        f"{history_file.stem}.{now.strftime(ARCHIVE_TS_FORMAT)}{ARCHIVE_SUFFIX}"
    )
    tmp_archive = archive_path.with_suffix(archive_path.suffix + ".tmp")
    try:
        with gzip.open(tmp_archive, 'wt', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(archive_rows)
        os.replace(tmp_archive, archive_path)
    except (OSError, IOError):
        tmp_archive.unlink(missing_ok=True)
        return None

    tmp_active = history_file.with_suffix(history_file.suffix + ".tmp")
    try:
        with open(tmp_active, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(keep_rows)
        os.replace(tmp_active, history_file)
    except (OSError, IOError):
        tmp_active.unlink(missing_ok=True)
        # Archive already landed; active file is untouched (still valid).

    return archive_path


def apply_retention(
    history_file: Path,
    *,
    max_age_days: float = DEFAULT_RETENTION_DAYS,
    now: Optional[datetime] = None,
) -> List[Path]:
    """Delete rotated archives older than max_age_days. Returns deleted paths.

    Age is taken from the rotation timestamp encoded in each archive's
    filename; falls back to file mtime if the name doesn't parse (e.g. a
    manually renamed archive).
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=max_age_days)
    deleted: List[Path] = []
    for archive in _archive_glob(history_file):
        ts = _parse_archive_timestamp(archive, history_file)
        if ts is None:
            try:
                ts = datetime.fromtimestamp(archive.stat().st_mtime)
            except OSError:
                continue
        if ts < cutoff:
            try:
                archive.unlink()
                deleted.append(archive)
            except OSError:
                pass
    return deleted


def rotate_and_retain(
    history_file: Path,
    *,
    rotate_mb: float = DEFAULT_ROTATE_MB,
    max_age_days: float = DEFAULT_ROTATE_MAX_AGE_DAYS,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    keep_hours: float = ROTATION_KEEP_HOURS,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Run one rotate-then-retain pass. Cheap no-op below threshold — safe
    to call on a slow (e.g. hourly) cadence from the monitor daemon.

    Returns {"archived": Optional[Path], "deleted": List[Path]}.
    """
    now = now or datetime.now()
    archived = rotate_status_history(
        history_file, rotate_mb=rotate_mb, max_age_days=max_age_days,
        keep_hours=keep_hours, now=now,
    )
    deleted = apply_retention(history_file, max_age_days=retention_days, now=now)
    return {"archived": archived, "deleted": deleted}


def read_agent_status_history_range(
    start: datetime,
    end: datetime,
    history_file: Path,
    agent_name: Optional[str] = None,
) -> List[Tuple[datetime, str, str, str, str, str]]:
    """Read agent status history across [start, end], transparently
    including rotated .csv.gz archives.

    read_agent_status_history() only ever reads the active CSV, which is
    correct for the 3h/24h windowed callers (TUI timeline, parquet export)
    but would silently miss rotated-away history for a deep date-range
    query. This is that deep-history path — used by the web API's
    analytics date-range endpoint, the one caller that legitimately reads
    further back than the active file retains.

    Archives are skipped when their rotation timestamp is at or before
    `start` (all their rows are necessarily older than the range).
    """
    now = datetime.now()
    hours = max((now - start).total_seconds() / 3600.0, 0.0) + 1.0  # cover `start` with margin
    active = read_agent_status_history(hours=hours, agent_name=agent_name, history_file=history_file)
    entries = [e for e in active if start <= e[0] <= end]

    for archive in _archive_glob(history_file):
        ts = _parse_archive_timestamp(archive, history_file)
        if ts is not None and ts <= start:
            continue
        try:
            with gzip.open(archive, 'rt', newline='') as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for row in reader:
                    if len(row) < 3:
                        continue
                    try:
                        row_ts = datetime.fromisoformat(row[0])
                    except (ValueError, IndexError):
                        continue
                    if row_ts < start or row_ts > end:
                        continue
                    if agent_name is not None and row[1] != agent_name:
                        continue
                    entries.append((
                        row_ts, row[1], row[2],
                        row[3] if len(row) > 3 else '',
                        row[4] if len(row) > 4 else '',
                        row[5] if len(row) > 5 else '',
                    ))
        except (OSError, IOError):
            continue

    entries.sort(key=lambda e: e[0])
    return entries


def disk_usage_findings(tmux_session: str, threshold_mb: float = 5000.0) -> List[str]:
    """Flag agent_status_history.csv / event_loop_timing.csv disk usage over
    threshold_mb (#465, #468). agent_status_history's total combines the
    active file with its rotated archives.

    The default threshold is sized for the default 540-day archive horizon:
    at the write rate the issue reported (~40MB/day, ~10:1 gzip), 18 months
    of intentional archives lands near 2GB, so warn only well above that.

    Used by `overcode doctor` as a global (not per-agent) check — these are
    one shared pair of files per tmux session, not one per agent.
    """
    from .settings import get_agent_history_path, get_event_loop_timing_path

    findings: List[str] = []
    threshold_bytes = threshold_mb * 1024 * 1024

    history_path = get_agent_history_path(tmux_session)
    history_total = history_path.stat().st_size if history_path.exists() else 0
    history_total += sum(
        a.stat().st_size for a in _archive_glob(history_path) if a.exists()
    )
    if history_total > threshold_bytes:
        findings.append(
            f"{history_path} (+ archives) is {history_total / (1024 * 1024):.0f}MB — "
            f"tune history_retention.status_history_max_days / "
            f"status_history_rotate_mb in config.yaml"
        )

    diag_path = get_event_loop_timing_path(tmux_session)
    if diag_path.exists():
        diag_size = diag_path.stat().st_size
        if diag_size > threshold_bytes:
            findings.append(
                f"{diag_path} is {diag_size / (1024 * 1024):.0f}MB — tune "
                f"history_retention.event_loop_timing_cap_mb (or set "
                f"event_loop_timing_enabled: false) in config.yaml"
            )

    return findings
