"""
Pure formatting functions for display.

These functions convert values (seconds, tokens, costs, etc.) into
human-readable strings. They have no domain logic — just formatting.

Extracted from tui_helpers.py to separate formatting from business logic.
"""

import re
import subprocess
from datetime import datetime
from typing import Optional, Tuple


def format_interval(seconds: int) -> str:
    """Format integer interval to human readable (s/m/h) without decimals.

    Use for displaying fixed intervals like polling rates: "@30s", "@1m"
    For durations with precision (e.g., work times), use format_duration().

    Examples: 30 -> "30s", 60 -> "1m", 3600 -> "1h"
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    else:
        return f"{seconds // 3600}h"


def format_ago(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Format datetime as time ago string.

    Args:
        dt: The datetime to format
        now: Reference time (defaults to datetime.now())

    Returns:
        String like "30s ago", "5m ago", "2.5h ago", or "never"
    """
    if not dt:
        return "never"
    if now is None:
        now = datetime.now()
    delta = (now - dt).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    elif delta < 3600:
        return f"{int(delta // 60)}m ago"
    else:
        return f"{delta / 3600:.1f}h ago"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable (s/m/h/d).

    Shows one decimal place for all units except seconds.
    Examples: 45s, 6.3m, 2.5h, 1.2d
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    elif seconds < 86400:  # Less than 1 day
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"


def format_tokens(tokens: int) -> str:
    """Format token count to human readable (K/M).

    Args:
        tokens: Number of tokens

    Returns:
        Formatted string like "1.2K", "3.5M", or "500" for small counts
    """
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    else:
        return str(tokens)


def format_cost(cost_usd: float) -> str:
    """Format cost in USD to human readable with stable width.

    Uses 1 decimal place and K/M suffixes for large amounts.
    Prefixed with $ symbol.

    Args:
        cost_usd: Cost in US dollars

    Returns:
        Formatted string like "$0.1", "$12.3", "$1.2K", "$3.5M"
    """
    if cost_usd >= 1_000_000:
        return f"${cost_usd / 1_000_000:.1f}M"
    elif cost_usd >= 1_000:
        return f"${cost_usd / 1_000:.1f}K"
    elif cost_usd >= 100:
        return f"${cost_usd:.0f}"
    elif cost_usd >= 10:
        return f"${cost_usd:.1f}"
    else:
        return f"${cost_usd:.2f}"


def format_budget(cost_usd: float, budget_usd: float) -> str:
    """Format cost with budget context (#173).

    Args:
        cost_usd: Current cost in USD
        budget_usd: Budget limit in USD (0 = no budget)

    Returns:
        Formatted string like "$1.23/$5.00" or "$1.23" if no budget
    """
    if budget_usd <= 0:
        return format_cost(cost_usd)
    return f"{format_cost(cost_usd)}/{format_cost(budget_usd)}"


def format_line_count(count: int) -> str:
    """Format line count (insertions/deletions) to human readable (K/M).

    Args:
        count: Number of lines

    Returns:
        Formatted string like "1.5K", "173K", "1.2M", or "500" for small counts.
        Uses one decimal for values under 10K, integer for 10K+ to stay within
        4 chars for layout alignment.
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 10_000:
        return f"{count // 1_000}K"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    else:
        return str(count)


def calculate_uptime(start_time: str, now: Optional[datetime] = None) -> str:
    """Calculate uptime from ISO format start_time.

    Args:
        start_time: ISO format datetime string
        now: Reference time (defaults to datetime.now())

    Returns:
        String like "30m", "4.5h", "2.5d", or "0m" on error
    """
    try:
        if now is None:
            now = datetime.now()
        start = datetime.fromisoformat(start_time)
        delta = now - start
        hours = delta.total_seconds() / 3600
        if hours < 1:
            minutes = delta.total_seconds() / 60
            return f"{int(minutes)}m"
        elif hours < 24:
            return f"{hours:.1f}h"
        else:
            days = hours / 24
            return f"{days:.1f}d"
    except (ValueError, AttributeError, TypeError):
        return "0m"


def truncate_name(name: str, max_len: int = 14) -> str:
    """Truncate and pad name for display.

    Args:
        name: Name to truncate
        max_len: Maximum length (default 14 for timeline view)

    Returns:
        Name truncated and left-justified to max_len
    """
    return name[:max_len].ljust(max_len)


def get_git_diff_stats(directory: str) -> Optional[Tuple[int, int, int]]:
    """Get git diff stats for a directory.

    Args:
        directory: Path to the git repository

    Returns:
        Tuple of (files_changed, insertions, deletions) or None if not a git repo
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        # Parse the last line which looks like:
        # "3 files changed, 10 insertions(+), 5 deletions(-)"
        # or just "1 file changed, 2 insertions(+)"
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[-1]:
            return (0, 0, 0)  # No changes

        summary = lines[-1]
        files = 0
        insertions = 0
        deletions = 0

        files_match = re.search(r'(\d+) files? changed', summary)
        ins_match = re.search(r'(\d+) insertions?', summary)
        del_match = re.search(r'(\d+) deletions?', summary)

        if files_match:
            files = int(files_match.group(1))
        if ins_match:
            insertions = int(ins_match.group(1))
        if del_match:
            deletions = int(del_match.group(1))

        return (files, insertions, deletions)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
