"""
Agent status history tracking.

Provides functions to log and read agent status history for timeline visualization.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

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

    if not path.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=hours)
    history: List[Tuple[datetime, str, str, str]] = []

    try:
        with open(path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = datetime.fromisoformat(row['timestamp'])
                    if ts >= cutoff:
                        agent = row['agent']
                        if agent_name is None or agent == agent_name:
                            history.append((
                                ts,
                                agent,
                                row['status'],
                                row.get('activity', '')
                            ))
                except (ValueError, KeyError):
                    continue
    except (OSError, IOError):
        pass

    return history


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
