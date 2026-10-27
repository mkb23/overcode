"""
Summarizer component for generating agent activity summaries.

Uses GPT-4o-mini to summarize what each agent has been doing and
their current halt state if not running.

Note: The summarizer now lives in the TUI, not the daemon.
This ensures zero API costs when the TUI is closed (no one would see the summaries anyway).
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING

from .status_constants import DEFAULT_CAPTURE_LINES
from .status_patterns import is_status_bar_line, strip_ansi
from .summarizer_client import SummarizerClient
from .tmux_utils import PaneInfo, pane_for_window

if TYPE_CHECKING:
    from .protocols import TmuxInterface

logger = logging.getLogger(__name__)

# A line that ticks while nothing else moves: the agent's busy line
# ("✶ Pondering… (34s · ↑ 1.2k tokens · esc to interrupt)"). Its digits and
# the spinner glyph change every frame; they say nothing a summary needs.
_TICKING_LINE = re.compile(
    r"esc to interrupt|\(\s*\d+(?:m\s*\d+)?s\b|\d+(?:\.\d+)?k?\s+tokens", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_LEADING_GLYPHS = re.compile(r"^[^\w\s(]+\s*")

# A pane whose listing has not moved is captured again after this long
# anyway (a wall-clock step backstop); inside it the last text is reused.
CAPTURE_KEEPALIVE_SECONDS = 60.0


def normalise_for_change(content: str, lines: int = DEFAULT_CAPTURE_LINES) -> str:
    """The pane as far as "has anything worth summarising changed?" goes.

    ANSI codes, status-bar chrome and the busy line's ticking counters and
    spinner glyph are dropped, and only the last ``lines`` lines count, so a
    pane whose only motion is a spinner frame or an elapsed-seconds counter
    hashes the same and costs no API call (#528, audit R16).
    """
    out = []
    for line in strip_ansi(content).rstrip().split("\n")[-lines:]:
        if is_status_bar_line(line):
            continue
        if _TICKING_LINE.search(line):
            line = _LEADING_GLYPHS.sub("", _DIGITS.sub("#", line.strip()))
        out.append(line.rstrip())
    return "\n".join(out)


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
    context_interval: float = 15.0  # Seconds between context summary updates (less frequent)
    lines: int = DEFAULT_CAPTURE_LINES  # Pane lines to capture
    max_tokens: int = 150  # Max response tokens
    idle_timeout: float = 300.0  # Auto-disable after 5 minutes of no TUI keypresses
    cost_cap: float = 100.0  # Per-TUI-launch cost cap in USD; requires restart to reset


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
            from .implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux
            tmux = RealTmux(command_timeout=TMUX_COMMAND_TIMEOUT_SECONDS)
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

        # Normalised-content hashes each summary kind was last made from:
        # a summary is only asked for again once its pane has changed
        self._last_content_hash: Dict[str, int] = {}  # short summary
        self._last_context_hash: Dict[str, int] = {}

        # The pass's one ``list-panes`` (window -> PaneInfo), and per session
        # the last capture: (signature, listed at, monotonic, text). A pane
        # whose signature has not moved is not captured again (#528).
        self._panes: Optional[Dict[str, PaneInfo]] = None
        self._listed_at: float = 0.0
        self._captured: Dict[str, Tuple[tuple, float, float, str]] = {}
        self.captures = 0

        # Where the next update() pass starts. A pass cut short by
        # should_stop resumes here, so a fleet too large to cover in one
        # pass is visited round-robin instead of re-summarising the same
        # first agents every tick while the tail starves.
        self._next_index: int = 0

        # Stats
        self.total_calls = 0
        self.total_tokens = 0
        self.total_cost_usd: float = 0.0
        self.cost_cap_hit: bool = False

    @property
    def available(self) -> bool:
        """Check if summarizer is available (API key present)."""
        return SummarizerClient.is_available()

    @property
    def enabled(self) -> bool:
        """Check if summarizer is currently enabled."""
        return self.config.enabled and self._client is not None

    def update(
        self, sessions, should_stop: Optional[Callable[[], bool]] = None
    ) -> Dict[str, AgentSummary]:
        """Update summaries for all sessions.

        Args:
            sessions: List of Session objects from SessionManager
            should_stop: Optional callable checked between agents; when it
                returns True the pass stops early. Summaries already produced
                are kept and the next pass resumes from the agent that was
                about to run, so every agent still gets its turn.

        Returns:
            Dict mapping session_id to AgentSummary
        """
        if not self.enabled:
            return self.summaries

        self._list_panes()
        count = len(sessions)
        start = self._next_index % count if count else 0
        for offset in range(count):
            if should_stop is not None and should_stop():
                self._next_index = (start + offset) % count
                break
            self._update_session(sessions[(start + offset) % count])
        else:
            self._next_index = start

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

        # Pane content: captured only if the pane may have changed
        content = self._content_for(session)
        if not content:
            return

        # Each kind of summary is asked for only when the pane has changed
        # since that kind was last made: spinner frames and ticking counters
        # are not changes (normalise_for_change)
        content_hash = hash(normalise_for_change(content, self.config.lines))
        need_short = need_short and self._last_content_hash.get(session_id) != content_hash
        need_context = need_context and self._last_context_hash.get(session_id) != content_hash
        if not need_short and not need_context:
            return

        # Get or create summary object
        prev_summary = self.summaries.get(session_id)
        if not prev_summary:
            prev_summary = AgentSummary()
            self.summaries[session_id] = prev_summary

        # Get current detected status
        status = "unknown"
        if current_status:
            status = getattr(current_status, 'current_state', 'unknown')

        if need_short and self._update_short_summary(session, prev_summary, content, status, now):
            self._last_content_hash[session_id] = content_hash

        if need_context and self._update_context_summary(session, prev_summary, content, status, now):
            self._last_context_hash[session_id] = content_hash

    def _update_short_summary(
        self, session, summary: AgentSummary, content: str, status: str, now: datetime
    ) -> bool:
        """Update the short (current activity) summary."""
        return self._update_summary(session, summary, content, status, now, mode="short")

    def _update_context_summary(
        self, session, summary: AgentSummary, content: str, status: str, now: datetime
    ) -> bool:
        """Update the context (wider context) summary."""
        return self._update_summary(session, summary, content, status, now, mode="context")

    def _update_summary(
        self, session, summary: AgentSummary, content: str, status: str, now: datetime,
        mode: str = "short",
    ) -> bool:
        """Update a summary field (short or context) via the AI client; True if the call succeeded."""
        is_short = mode == "short"
        previous = summary.text if is_short else summary.context
        max_tokens = 50 if is_short else 75

        try:
            result = self._client.summarize(
                pane_content=content,
                previous_summary=previous,
                current_status=status,
                lines=self.config.lines,
                max_tokens=max_tokens,
                mode=mode,
            )

            self.total_calls += 1
            self._accumulate_cost()

            if result and result.strip().upper() != "UNCHANGED":
                stripped = result.strip()
                if is_short:
                    summary.text = stripped
                    summary.updated_at = now.isoformat()
                else:
                    summary.context = stripped
                    summary.context_updated_at = now.isoformat()
                logger.debug(f"Updated {mode} summary for {session.name}: {stripped[:50]}...")

            if is_short:
                self._last_update[session.id] = now
            else:
                self._last_context_update[session.id] = now
            return True

        except Exception as e:
            logger.warning(f"{mode.capitalize()} summary error for {session.name}: {e}")
            return False

    def _accumulate_cost(self) -> None:
        """Accumulate cost from the most recent API call."""
        if not self._client:
            return
        input_tokens = getattr(self._client, 'last_input_tokens', 0)
        output_tokens = getattr(self._client, 'last_output_tokens', 0)
        model = getattr(self._client, 'model', None)
        if (input_tokens or output_tokens) and isinstance(model, str):
            from .pricing import estimate_cost
            self.total_cost_usd += estimate_cost(model, input_tokens, output_tokens)

    def _list_panes(self) -> None:
        """One ``list-panes`` for the pass: every window's change signature."""
        self._listed_at = time.time()
        try:
            panes = self.tmux.list_panes(self.tmux_session)
        except Exception:
            panes = None
        self._panes = panes if isinstance(panes, dict) else None

    def _content_for(self, session) -> Optional[str]:
        """The session's pane text: the last capture while the pane is unchanged.

        Unchanged means the listing's signature equals the one listed
        before the last capture and its activity second is strictly before
        the second that listing was taken in: output after that listing
        would carry a later second. Without a listing every call captures.
        """
        window = session.tmux_window
        pane = pane_for_window(self._panes, str(window)) if self._panes else None
        record = self._captured.get(session.id)
        mono = time.monotonic()
        if (pane is not None and record is not None
                and pane.signature == record[0]
                and pane.activity < int(record[1])
                and mono - record[2] < CAPTURE_KEEPALIVE_SECONDS):
            return record[3]
        content = self._capture_pane(window)
        self.captures += 1
        if pane is not None and content:
            self._captured[session.id] = (pane.signature, self._listed_at, mono, content)
        else:
            self._captured.pop(session.id, None)
        return content

    def _capture_pane(self, window: str) -> Optional[str]:
        """Capture pane content for summarization.

        Args:
            window: tmux window name

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

        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"Failed to capture pane {window}: {e}")
            return None

    def stop(self) -> None:
        """Clean up resources."""
        if self._client:
            self._client.close()
            self._client = None
