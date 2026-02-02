"""
Pure business logic for Supervisor Daemon.

These functions contain no I/O and are fully unit-testable.
They are used by SupervisorDaemon but can be tested independently.
"""

from typing import List, Optional

from .status_constants import STATUS_RUNNING, get_status_emoji


def build_daemon_claude_context(
    tmux_session: str,
    non_green_sessions: List[dict],
) -> str:
    """Build initial context prompt for daemon claude.

    Pure function - no side effects, fully testable.

    Args:
        tmux_session: Name of the tmux session
        non_green_sessions: List of session dicts with 'name', 'tmux_window',
                           'standing_instructions', 'current_status', 'repo_name'

    Returns:
        Multi-line context string for daemon claude
    """
    context_parts = []

    context_parts.append("You are the Overcode daemon claude agent.")
    context_parts.append("Your mission: Make all RED/YELLOW/ORANGE sessions GREEN.")
    context_parts.append("")
    context_parts.append(f"TMUX SESSION: {tmux_session}")
    context_parts.append(f"Sessions needing attention: {len(non_green_sessions)}")
    context_parts.append("")

    for session in non_green_sessions:
        status = session.get("current_status", "unknown")
        emoji = get_status_emoji(status)
        name = session.get("name", "unknown")
        window = session.get("tmux_window", "?")
        context_parts.append(f"{emoji} {name} (window {window})")

        instructions = session.get("standing_instructions")
        if instructions:
            context_parts.append(f"   Autopilot: {instructions}")
        else:
            context_parts.append("   No autopilot instructions set")

        repo_name = session.get("repo_name")
        if repo_name:
            context_parts.append(f"   Repo: {repo_name}")
        context_parts.append("")

    context_parts.append("Read the daemon claude skill for how to control sessions via tmux.")
    context_parts.append("Start by reading ~/.overcode/sessions/sessions.json to see full state.")
    context_parts.append("Then check each non-green session and help them make progress.")

    return "\n".join(context_parts)


def filter_non_green_sessions(
    sessions: List[dict],
    exclude_names: Optional[List[str]] = None,
) -> List[dict]:
    """Filter sessions to only those needing attention.

    Pure function - no side effects, fully testable.

    Filters out:
    - Running (green) sessions
    - Sessions with names in exclude_names (e.g., 'daemon_claude')
    - Asleep sessions
    - Sessions with DO_NOTHING standing orders

    Args:
        sessions: List of session dicts with 'current_status', 'name',
                 'is_asleep', 'standing_instructions'
        exclude_names: Optional list of session names to always exclude

    Returns:
        Filtered list of sessions needing attention
    """
    exclude_names = exclude_names or []
    result = []

    for s in sessions:
        # Skip green sessions
        if s.get("current_status") == STATUS_RUNNING:
            continue

        # Skip excluded names (e.g., daemon_claude)
        if s.get("name") in exclude_names:
            continue

        # Skip asleep sessions
        if s.get("is_asleep", False):
            continue

        # Skip sessions with DO_NOTHING standing orders
        instructions = s.get("standing_instructions", "")
        if instructions and "DO_NOTHING" in instructions.upper():
            continue

        result.append(s)

    return result


def calculate_daemon_claude_run_seconds(
    started_at_iso: Optional[str],
    now_iso: str,
    previous_total: float,
) -> float:
    """Calculate total daemon claude run time including current run.

    Pure function - no side effects, fully testable.

    Args:
        started_at_iso: ISO timestamp when current run started (None if not running)
        now_iso: Current time as ISO timestamp
        previous_total: Previously accumulated run seconds

    Returns:
        Total run seconds including any current run
    """
    if started_at_iso is None:
        return previous_total

    try:
        from datetime import datetime
        started_at = datetime.fromisoformat(started_at_iso)
        now = datetime.fromisoformat(now_iso)
        current_run = (now - started_at).total_seconds()
        return previous_total + max(0, current_run)
    except (ValueError, TypeError):
        return previous_total


def should_launch_daemon_claude(
    non_green_sessions: List[dict],
    daemon_claude_running: bool,
) -> tuple:
    """Determine if daemon claude should be launched.

    Pure function - no side effects, fully testable.

    Args:
        non_green_sessions: List of non-green session dicts
        daemon_claude_running: Whether daemon claude is already running

    Returns:
        Tuple of (should_launch: bool, reason: str)
    """
    if not non_green_sessions:
        return False, "no_sessions"

    if daemon_claude_running:
        return False, "already_running"

    # Check if all are waiting for user with no instructions
    all_waiting_user = all(
        s.get("current_status") == "waiting_user"
        for s in non_green_sessions
    )
    any_has_instructions = any(
        s.get("standing_instructions")
        for s in non_green_sessions
    )

    if all_waiting_user and not any_has_instructions:
        return False, "waiting_user_no_instructions"

    reason = "with_instructions" if any_has_instructions else "non_user_blocked"
    return True, reason


def parse_intervention_log_line(
    line: str,
    session_names: List[str],
    action_phrases: List[str],
    no_action_phrases: List[str],
) -> Optional[str]:
    """Parse a log line to extract intervention session name if applicable.

    Pure function - no side effects, fully testable.

    Args:
        line: Single log line to parse
        session_names: Session names to look for
        action_phrases: Phrases indicating an action was taken
        no_action_phrases: Phrases indicating no action was taken

    Returns:
        Session name if an intervention was detected, None otherwise
    """
    line_lower = line.lower()

    for name in session_names:
        if f"{name} - " in line:
            # Check for no-action phrases first
            if any(phrase in line_lower for phrase in no_action_phrases):
                return None

            # Check for action phrases
            if any(phrase in line_lower for phrase in action_phrases):
                return name

    return None
