"""Find an agent's own files from a test that knows only its name (item 35).

An agent's hook state, event log and report live in
``<state dir>/<tmux session>/agents/<session id>/``; e2e and live tests
know the agent's name, so the id is looked up in sessions.json.
"""

import json
from pathlib import Path
from typing import Optional

from overcode.paths import agent_path


def agent_session_id(state_dir, tmux_session: str, name: str) -> Optional[str]:
    """The session id of the agent ``name`` in ``state_dir``'s sessions.json, or None."""
    for candidate in (Path(state_dir) / "sessions.json", Path(state_dir) / "sessions" / "sessions.json"):
        try:
            records = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        for record in records.values():
            if record.get("name") == name and record.get("tmux_session") == tmux_session:
                return record.get("id")
    return None


def agent_state_file(state_dir, tmux_session: str, name: str, kind: str) -> Path:
    """Where agent ``name``'s ``kind`` file is (``paths.agent_path``, under ``state_dir``)."""
    sid = agent_session_id(state_dir, tmux_session, name)
    return agent_path(tmux_session, sid, name, kind, base=Path(state_dir) / tmux_session)
