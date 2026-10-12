"""Helpers every non-Claude stats reader shares.

Two kinds live here:

* Which conversations are this agent's. ``agent_hook_ids`` is the one
  ownership-aware hook-state lookup: it trusts a hook-reported id only when
  the agent's session record already holds it (or the reader can vouch for
  it), so a ``hook_state_<name>.json`` left by a killed agent of the same
  name cannot pin a relaunched agent to the dead agent's conversation. The
  readers fall back to "a conversation in this directory" only when the
  agent's own hooks have never reported, and then skip any id another
  overcode agent owns (``others_owned_ids``).
* Small coercion, SQLite and JSONL helpers that were copied verbatim into
  each reader.

Nothing here imports the rest of overcode, so ``stats_reader`` can use it
without an import cycle.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

# -- the session record ----------------------------------------------------


def owned_ids(session: Any) -> List[str]:
    """The backend session ids the agent's record holds, active last."""
    ids = list(getattr(session, "agent_session_ids", None) or [])
    active = getattr(session, "active_agent_session_id", None)
    if active and active not in ids:
        ids.append(active)
    return [sid for sid in ids if isinstance(sid, str) and sid]


def others_owned_ids(session: Any, all_sessions: Sequence[Any]) -> FrozenSet[str]:
    """Every id another overcode agent's record holds.

    The set form of ``claude_pid.is_session_id_owned_by_others``: a reader
    filtering many directory rows tests each against it in O(1).
    """
    own_id = getattr(session, "id", None)
    return frozenset(
        sid
        for other in all_sessions
        if getattr(other, "id", None) != own_id
        for sid in (getattr(other, "agent_session_ids", None) or ())
    )


def directories(session: Any) -> List[str]:
    """Every spelling of the agent's working directory worth matching on.

    A backend records the cwd it was handed while overcode holds the
    configured path: on macOS ``/tmp`` and ``/private/tmp`` are the same
    place, and a symlinked project root is common.
    """
    directory = getattr(session, "start_directory", None)
    if not directory:
        return []
    candidates = [str(directory)]
    try:
        candidates.append(str(Path(directory).resolve()))
    except OSError:
        pass
    return list(dict.fromkeys(candidates))


def launch_datetime(session: Any) -> Optional[datetime]:
    """The agent's launch time, or None when the record has none."""
    start_time = getattr(session, "start_time", None)
    if not start_time:
        return None
    try:
        return datetime.fromisoformat(start_time)
    except (ValueError, TypeError):
        return None


# -- hook state ------------------------------------------------------------


def hook_state_path(session: Any) -> Optional[Path]:
    """Where ``hook_handler`` (or opencode's plugin) publishes the agent's hook state."""
    tmux_session = getattr(session, "tmux_session", None)
    name = getattr(session, "name", None)
    if not isinstance(tmux_session, str) or not isinstance(name, str):
        return None
    if not tmux_session or not name:
        return None
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    base = Path(state_dir) if state_dir else Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_state_{name}.json"


def read_hook_state(session: Any) -> Optional[dict]:
    """The agent's hook state, or None when absent or unreadable."""
    path = hook_state_path(session)
    if path is None:
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def hook_state_ids(state: Optional[dict]) -> List[str]:
    """Session ids a hook state names, the active one last."""
    if not state:
        return []
    raw = state.get("agent_session_ids")
    ids = [sid for sid in raw if isinstance(sid, str) and sid] if isinstance(raw, list) else []
    active = state.get("agent_session_id")
    if isinstance(active, str) and active:
        ids = [sid for sid in ids if sid != active] + [active]
    return ids


def _written_since_launch(state: dict, session: Any) -> bool:
    """Whether this agent's process can have written ``state``.

    A file last written before the agent launched was left by an earlier
    agent of the same name. Unknown on either side counts as this agent's:
    the caller then refuses to guess from the directory rather than guess
    wrong.
    """
    launched = launch_datetime(session)
    stamp = state.get("timestamp")
    if launched is None or not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return True
    return stamp >= launched.timestamp()


@dataclass(frozen=True)
class HookIds:
    """What an agent's hook state says about its conversations."""

    # Hook-reported ids that are this agent's, the current one last.
    own: Tuple[str, ...] = ()
    # The agent's own hooks have reported (hook state naming an id, written
    # since launch). While true the directory is not evidence of anything:
    # the hooks will name the conversation, and the newest row there may be
    # another Claude/codex/opencode's.
    live: bool = False


Vouch = Callable[[Sequence[str]], Iterable[str]]


def agent_hook_ids(session: Any, vouch: Optional[Vouch] = None) -> HookIds:
    """The hook-reported conversation ids that are this agent's.

    ``hook_handler`` records an id in sessions.json before writing it to hook
    state, so the agent's own hooks always pass the record check. A hook
    state file left by an earlier agent of the same name does not: its ids
    were never this record's, and adopting one would pin the new agent to a
    dead agent's conversation (#500 then rejects the real id as stale).

    ``vouch`` is for writers that do not go through ``hook_handler`` (the
    opencode plugins): called with the ids the record lacks, it returns the
    ones the reader can show are this agent's (e.g. a conversation created
    after the agent launched).
    """
    state = read_hook_state(session)
    ids = hook_state_ids(state)
    if not ids:
        return HookIds()
    known = set(owned_ids(session))
    unknown = [sid for sid in ids if sid not in known]
    vouched = set(vouch(unknown)) if (vouch is not None and unknown) else set()
    own = tuple(sid for sid in ids if sid in known or sid in vouched)
    return HookIds(own=own, live=_written_since_launch(state, session))


def hooks_live(session: Any) -> bool:
    """``agent_hook_ids(session).live`` without the ownership check."""
    state = read_hook_state(session)
    return bool(hook_state_ids(state)) and _written_since_launch(state, session)


# -- coercion --------------------------------------------------------------


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


# -- SQLite ----------------------------------------------------------------


def placeholders(count: int) -> str:
    return ",".join("?" * count)


def table_columns(conn: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return ()
    return tuple(row[1] for row in rows)


# -- JSONL -----------------------------------------------------------------


def iter_jsonl(path: Path) -> Iterator[Any]:
    """Each parseable line of ``path``; blank and corrupt lines are skipped."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (ValueError, TypeError):
                    continue
    except OSError:
        return
