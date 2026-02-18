"""
Sister integration — polls remote overcode instances for agent status.

Each sister is another machine running `overcode serve`. We poll their
/api/status endpoint and convert the agent data into virtual Session
objects that can be merged into the local TUI's session list.
"""

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import get_hostname, get_sisters_config
from .session_manager import Session, SessionStats


@dataclass
class SisterState:
    """Tracking state for a single sister instance."""

    name: str
    url: str
    api_key: str = ""
    reachable: bool = False
    last_fetch: Optional[str] = None  # ISO timestamp
    last_error: str = ""
    sessions: List[Session] = field(default_factory=list)
    green_agents: int = 0
    total_agents: int = 0
    total_cost: float = 0.0


class SisterPoller:
    """Polls sister overcode instances and converts their agents to Sessions."""

    def __init__(self) -> None:
        sisters_config = get_sisters_config()
        self._sisters: List[SisterState] = [
            SisterState(
                name=s["name"],
                url=s["url"],
                api_key=s.get("api_key", ""),
            )
            for s in sisters_config
        ]
        self.local_hostname: str = get_hostname()

    @property
    def has_sisters(self) -> bool:
        return len(self._sisters) > 0

    def poll_all(self) -> List[Session]:
        """Fetch all sisters sequentially, return combined virtual Sessions."""
        all_sessions: List[Session] = []
        for sister in self._sisters:
            sessions = self._poll_sister(sister)
            all_sessions.extend(sessions)
        return all_sessions

    def poll_single_agent(self, source_url: str, source_api_key: str, agent_name: str) -> Optional[Session]:
        """Fetch status for a single remote agent (lightweight — one pane capture).

        Args:
            source_url: Sister base URL (e.g., http://remote:15337)
            source_api_key: API key for authentication
            agent_name: Name of the agent to fetch

        Returns:
            Updated Session object, or None on failure
        """
        url = f"{source_url}/api/agents/{agent_name}/status"
        req = Request(url, method="GET")
        if source_api_key:
            req.add_header("X-API-Key", source_api_key)

        try:
            with urlopen(req, timeout=3) as resp:
                agent = json.loads(resp.read().decode("utf-8"))
        except (URLError, socket.timeout, json.JSONDecodeError, OSError):
            return None

        # Find the sister to get host_name
        host_name = agent_name  # fallback
        for sister in self._sisters:
            if sister.url == source_url:
                host_name = sister.name
                break

        return _agent_to_session(agent, host_name, source_url, source_api_key)

    def get_sister_states(self) -> List[SisterState]:
        """Return current state of all sisters (for status bar display)."""
        return list(self._sisters)

    def _poll_sister(self, sister: SisterState) -> List[Session]:
        """Fetch /api/status from a single sister, update its state."""
        url = f"{sister.url}/api/status"
        req = Request(url, method="GET")
        if sister.api_key:
            req.add_header("X-API-Key", sister.api_key)

        try:
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, socket.timeout, json.JSONDecodeError, OSError) as e:
            sister.reachable = False
            sister.last_error = str(e)
            sister.sessions = []
            sister.green_agents = 0
            sister.total_agents = 0
            sister.total_cost = 0.0
            return []

        sister.reachable = True
        sister.last_fetch = datetime.now().isoformat()
        sister.last_error = ""

        host_name = data.get("hostname", sister.name)
        agents = data.get("agents", [])
        summary = data.get("summary", {})

        sister.green_agents = summary.get("green_agents", 0)
        sister.total_agents = summary.get("total_agents", 0)

        sessions: List[Session] = []
        total_cost = 0.0
        for agent in agents:
            session = _agent_to_session(agent, host_name, sister.url, sister.api_key)
            sessions.append(session)
            total_cost += agent.get("cost_usd", 0.0)

        # Resolve parent hierarchy: parent_name → parent_session_id (#244)
        name_to_id = {s.name: s.id for s in sessions}
        for i, agent in enumerate(agents):
            parent_name = agent.get("parent_name", "")
            if parent_name and parent_name in name_to_id:
                sessions[i].parent_session_id = name_to_id[parent_name]

        sister.sessions = sessions
        sister.total_cost = total_cost
        return sessions


def _agent_to_session(agent: dict, host_name: str, source_url: str = "", source_api_key: str = "") -> Session:
    """Convert an API agent dict to a virtual Session object."""
    name = agent.get("name", "unknown")
    session_id = f"remote:{host_name}:{name}"

    # Map API status to session status — pass through as-is so that
    # filtering (hide done, hide asleep) and display (emoji, color, sort)
    # work correctly for remote agents.
    status = agent.get("status", "running")
    is_asleep = status == "asleep"

    # Build SessionStats from API fields
    stats = SessionStats(
        estimated_cost_usd=agent.get("cost_usd", 0.0),
        total_tokens=agent.get("tokens_raw", 0),
        current_state=status,
        current_task=agent.get("activity", ""),
        green_time_seconds=agent.get("green_time_raw", 0.0),
        non_green_time_seconds=agent.get("non_green_time_raw", 0.0),
        sleep_time_seconds=agent.get("sleep_time_raw", 0.0),
        steers_count=agent.get("robot_steers", 0),
        interaction_count=agent.get("human_interactions", 0) + agent.get("robot_steers", 0),
    )

    # Parse state_since from time_in_state_raw
    state_since = None
    time_in_state_raw = agent.get("time_in_state_raw", 0)
    if time_in_state_raw > 0:
        state_since = (
            datetime.now()
            - __import__("datetime").timedelta(seconds=time_in_state_raw)
        ).isoformat()
        stats.state_since = state_since

    # Use actual start_time from API if available
    start_time = agent.get("start_time", "") or datetime.now().isoformat()

    return Session(
        id=session_id,
        name=name,
        tmux_session="",  # No local tmux session
        tmux_window=0,
        command=[],
        start_directory=None,
        start_time=start_time,
        repo_name=agent.get("repo", "") or None,
        branch=agent.get("branch", "") or None,
        status=status,
        is_asleep=is_asleep,
        permissiveness_mode=agent.get("permissiveness_mode", "normal"),
        standing_instructions="(remote)" if agent.get("standing_orders") else "",
        standing_orders_complete=agent.get("standing_orders_complete", False),
        stats=stats,
        cost_budget_usd=agent.get("cost_budget_usd", 0.0),
        is_asleep=agent.get("is_asleep", False),
        time_context_enabled=agent.get("time_context_enabled", False),
        human_annotation=agent.get("human_annotation", ""),
        # Heartbeat
        heartbeat_enabled=agent.get("heartbeat_enabled", False),
        heartbeat_frequency_seconds=agent.get("heartbeat_frequency_seconds", 300),
        heartbeat_paused=agent.get("heartbeat_paused", False),
        last_heartbeat_time=agent.get("last_heartbeat_time"),
        # Sister metadata
        is_remote=True,
        source_host=host_name,
        source_url=source_url,
        source_api_key=source_api_key,
        pane_content=agent.get("pane_content", ""),
        remote_git_diff=_parse_git_diff(agent),
        remote_median_work_time=_parse_median_work(agent),
        # AI summaries from remote summarizer
        remote_activity_summary=agent.get("activity_summary", ""),
        remote_activity_summary_context=agent.get("activity_summary_context", ""),
    )


def _parse_git_diff(agent: dict) -> tuple | None:
    """Extract git diff stats tuple from API agent dict."""
    files = agent.get("git_diff_files", 0)
    ins = agent.get("git_diff_insertions", 0)
    dels = agent.get("git_diff_deletions", 0)
    if files or ins or dels:
        return (files, ins, dels)
    return None


def _parse_median_work(agent: dict) -> float:
    """Extract median work time (seconds) from API agent dict."""
    raw = agent.get("median_work_time_raw", 0.0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0
