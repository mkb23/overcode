"""
Session ID ownership guard for Claude Code sessions.

Prevents cross-contamination when multiple agents share the same working
directory by checking that a discovered sessionId isn't already owned by
another agent.

The primary mechanism is --session-id prescribed at launch (#373).
This module provides the ownership guard used by sync_session_id's
history.jsonl fallback for post-/clear detection.
"""


def is_session_id_owned_by_others(
    session_id: str,
    own_agent_id: str,
    all_sessions: list,
) -> bool:
    """Check if a Claude session ID is already owned by another agent.

    Prevents cross-contamination when the directory-based lookup discovers
    a sessionId that belongs to a different agent.

    Args:
        session_id: The Claude sessionId to check.
        own_agent_id: The overcode agent ID doing the check.
        all_sessions: All active overcode sessions to check against.

    Returns:
        True if another agent already owns this sessionId.
    """
    for session in all_sessions:
        if session.id == own_agent_id:
            continue
        owned_ids = getattr(session, 'claude_session_ids', None) or []
        if session_id in owned_ids:
            return True
    return False
