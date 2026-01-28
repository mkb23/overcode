"""
Summarizer component for generating agent activity summaries.

Uses GPT-4o-mini to summarize what each agent has been doing and
their current halt state if not running.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

from .summarizer_client import SummarizerClient
from .settings import get_session_dir

if TYPE_CHECKING:
    from .interfaces import TmuxInterface

logger = logging.getLogger(__name__)


@dataclass
class AgentSummary:
    """Summary for a single agent."""

    # Short summary - current activity (~50 chars)
    text: str = ""
    updated_at: Optional[str] = None  # ISO timestamp
    tokens_used: int = 0

    # Context summary - wider context (~80 chars)
    context: str = ""
    context_updated_at: Optional[str] = None  # ISO timestamp


@dataclass
class SummarizerConfig:
    """Configuration for the summarizer."""

    enabled: bool = False  # Off by default
    interval: float = 5.0  # Seconds between short summary updates per agent
    context_interval: float = 30.0  # Seconds between context summary updates (less frequent)
    lines: int = 200  # Pane lines to capture
    max_tokens: int = 150  # Max response tokens


class SummarizerComponent:
    """Component for generating agent activity summaries.

    Follows the daemon component pattern (like PresenceComponent).
    Gracefully degrades if OPENAI_API_KEY is not available.
    """

    def __init__(
        self,
        tmux_session: str,
        tmux: "TmuxInterface" = None,
        config: Optional[SummarizerConfig] = None,
    ):
        """Initialize the summarizer component.

        Args:
            tmux_session: Name of the tmux session
            tmux: TmuxInterface for pane capture (defaults to RealTmux)
            config: SummarizerConfig (defaults to disabled)
        """
        self.tmux_session = tmux_session
        self.config = config or SummarizerConfig()

        # Dependency injection for testability
        if tmux is None:
            from .interfaces import RealTmux
            tmux = RealTmux()
        self.tmux = tmux

        # Initialize client (gracefully handles missing API key)
        self._client: Optional[SummarizerClient] = None
        if self.config.enabled and SummarizerClient.is_available():
            self._client = SummarizerClient()

        # Per-agent summaries
        self.summaries: Dict[str, AgentSummary] = {}

        # Rate limiting per session (separate for short and context)
        self._last_update: Dict[str, datetime] = {}
        self._last_context_update: Dict[str, datetime] = {}

        # Content hashes for change detection (avoid API calls when nothing changed)
        self._last_content_hash: Dict[str, int] = {}

        # Stats
        self.total_calls = 0
        self.total_tokens = 0

        # Control file path
        self._control_file = get_session_dir(tmux_session) / "summarizer_control.json"

    @property
    def available(self) -> bool:
        """Check if summarizer is available (API key present)."""
        return SummarizerClient.is_available()

    @property
    def enabled(self) -> bool:
        """Check if summarizer is currently enabled."""
        return self.config.enabled and self._client is not None

    def check_control_file(self) -> None:
        """Check control file for enable/disable commands."""
        if not self._control_file.exists():
            return

        try:
            with open(self._control_file) as f:
                data = json.load(f)

            should_enable = data.get("enabled", False)

            if should_enable and not self.config.enabled:
                # Enable requested
                if SummarizerClient.is_available():
                    self.config.enabled = True
                    self._client = SummarizerClient()
                    logger.info("Summarizer enabled via control file")
                else:
                    logger.warning("Summarizer enable requested but OPENAI_API_KEY not set")
            elif not should_enable and self.config.enabled:
                # Disable requested
                self.config.enabled = False
                if self._client:
                    self._client.close()
                    self._client = None
                logger.info("Summarizer disabled via control file")

        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Error reading summarizer control file: {e}")

    def update(self, sessions) -> Dict[str, AgentSummary]:
        """Update summaries for all sessions.

        Args:
            sessions: List of Session objects from SessionManager

        Returns:
            Dict mapping session_id to AgentSummary
        """
        # Check control file for enable/disable
        self.check_control_file()

        if not self.enabled:
            return self.summaries

        for session in sessions:
            self._update_session(session)

        return self.summaries

    def _update_session(self, session) -> None:
        """Update summaries for a single session.

        Generates two types of summaries:
        - Short: current activity (updated frequently)
        - Context: wider context (updated less frequently)

        Args:
            session: Session object with id, tmux_window, current_status
        """
        if not self._client:
            return

        session_id = session.id
        now = datetime.now()

        # Check rate limits for each summary type
        last_short = self._last_update.get(session_id)
        last_context = self._last_context_update.get(session_id)

        short_elapsed = (now - last_short).total_seconds() if last_short else float('inf')
        context_elapsed = (now - last_context).total_seconds() if last_context else float('inf')

        need_short = short_elapsed >= self.config.interval
        need_context = context_elapsed >= self.config.context_interval

        if not need_short and not need_context:
            return

        # Skip terminated sessions
        current_status = getattr(session, 'stats', None)
        if current_status:
            status = getattr(current_status, 'current_state', 'unknown')
            if status == 'terminated':
                return

        # Capture pane content
        content = self._capture_pane(session.tmux_window)
        if not content:
            return

        # Check if content has actually changed (avoid unnecessary API calls)
        content_hash = hash(content)
        content_changed = True
        if session_id in self._last_content_hash:
            if self._last_content_hash[session_id] == content_hash:
                content_changed = False

        # If content hasn't changed, skip short summary but still allow context
        # (context changes less often so we're more lenient)
        if not content_changed and not need_context:
            return

        self._last_content_hash[session_id] = content_hash

        # Get or create summary object
        prev_summary = self.summaries.get(session_id)
        if not prev_summary:
            prev_summary = AgentSummary()
            self.summaries[session_id] = prev_summary

        # Get current detected status
        status = "unknown"
        if current_status:
            status = getattr(current_status, 'current_state', 'unknown')

        # Update short summary if needed and content changed
        if need_short and content_changed:
            self._update_short_summary(session, prev_summary, content, status, now)

        # Update context summary if needed (less frequent, runs even if content same)
        if need_context:
            self._update_context_summary(session, prev_summary, content, status, now)

    def _update_short_summary(
        self, session, summary: AgentSummary, content: str, status: str, now: datetime
    ) -> None:
        """Update the short (current activity) summary."""
        try:
            result = self._client.summarize(
                pane_content=content,
                previous_summary=summary.text,
                current_status=status,
                lines=self.config.lines,
                max_tokens=100,
                mode="short",
            )

            self.total_calls += 1

            if result and result.strip().upper() != "UNCHANGED":
                summary.text = result.strip()
                summary.updated_at = now.isoformat()
                logger.debug(f"Updated short summary for {session.name}: {result[:50]}...")

            self._last_update[session.id] = now

        except Exception as e:
            logger.warning(f"Short summary error for {session.name}: {e}")

    def _update_context_summary(
        self, session, summary: AgentSummary, content: str, status: str, now: datetime
    ) -> None:
        """Update the context (wider context) summary."""
        try:
            result = self._client.summarize(
                pane_content=content,
                previous_summary=summary.context,
                current_status=status,
                lines=self.config.lines,
                max_tokens=150,
                mode="context",
            )

            self.total_calls += 1

            if result and result.strip().upper() != "UNCHANGED":
                summary.context = result.strip()
                summary.context_updated_at = now.isoformat()
                logger.debug(f"Updated context summary for {session.name}: {result[:50]}...")

            self._last_context_update[session.id] = now

        except Exception as e:
            logger.warning(f"Context summary error for {session.name}: {e}")

    def _capture_pane(self, window: int) -> Optional[str]:
        """Capture pane content for summarization.

        Args:
            window: tmux window index

        Returns:
            Pane content string or None on error
        """
        try:
            content = self.tmux.capture_pane(
                self.tmux_session,
                window,
                lines=self.config.lines + 50,  # Capture extra for filtering
            )
            if not content:
                return None

            # Strip trailing blank lines and return last N lines
            lines = content.rstrip().split('\n')
            meaningful_lines = lines[-self.config.lines:] if len(lines) > self.config.lines else lines
            return '\n'.join(meaningful_lines)

        except Exception as e:
            logger.warning(f"Failed to capture pane {window}: {e}")
            return None

    def get_summary(self, session_id: str) -> Optional[AgentSummary]:
        """Get summary for a specific session.

        Args:
            session_id: Session ID

        Returns:
            AgentSummary or None if not available
        """
        return self.summaries.get(session_id)

    def stop(self) -> None:
        """Clean up resources."""
        if self._client:
            self._client.close()
            self._client = None


def get_summarizer_control_path(session: str) -> Path:
    """Get the summarizer control file path for a session."""
    return get_session_dir(session) / "summarizer_control.json"


def set_summarizer_enabled(session: str, enabled: bool) -> None:
    """Set summarizer enabled state via control file.

    The daemon reads this file each loop and adjusts accordingly.

    Args:
        session: tmux session name
        enabled: Whether to enable or disable
    """
    control_path = get_summarizer_control_path(session)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    with open(control_path, 'w') as f:
        json.dump({"enabled": enabled}, f)


def is_summarizer_enabled(session: str) -> bool:
    """Check if summarizer is enabled for a session.

    Args:
        session: tmux session name

    Returns:
        True if enabled, False otherwise
    """
    control_path = get_summarizer_control_path(session)
    if not control_path.exists():
        return False

    try:
        with open(control_path) as f:
            data = json.load(f)
        return data.get("enabled", False)
    except (json.JSONDecodeError, OSError):
        return False
