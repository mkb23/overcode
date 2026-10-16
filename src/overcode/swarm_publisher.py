"""The engine's virtual rows: what expanded parents' delegated work looks like now.

docs/design/agent-observability-0.6.1.md §6.2, phase 2. A view subscribes to
``swarm:<id>`` when the person expands a parent (``EngineServer.
swarm_subscriptions``); only those parents' rows are built and published, in
the snapshot's ``virtual`` section, so a collapsed swarm costs one rollup
delta per transition and nothing else.

Rows are rebuilt with every publish (full and quick ticks, from the hook
state the detector just read), and between ticks when:

* the subscriptions change (a parent expanded or collapsed);
* a finished row's time is up (``swarm.virtual_rows`` returns the deadline);
* a watched workflow journal moved (one stat per live run of an expanded
  parent, at the wake scan's cadence: phase progress within a second).

Rows hold absolute times and counts, so a quiet swarm builds the same rows
and the engine's diff sends nothing.

A second row class (Phase 3's adopted background sessions) is not virtual:
those are ordinary agent views. This module only ever fills ``virtual``.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, Optional

from . import swarm
from .swarm_files import SwarmFiles


def finished_row_seconds() -> float:
    from .config import get_observability_config

    return get_observability_config()["finished_row_seconds"]


class SwarmPublisher:
    """Builds the subscribed parents' virtual rows; says when they are due again."""

    def __init__(
        self,
        swarm_state: Callable[[str], Optional[dict]],
        conversations: Callable[[str], Optional[dict]],
        keep_seconds: Callable[[], float] = finished_row_seconds,
        files: Optional[SwarmFiles] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.swarm_state = swarm_state          # agent name -> hook state (or None)
        self.conversations = conversations      # agent id -> spend per conversation
        self.keep_seconds = keep_seconds
        self.files = files if files is not None else SwarmFiles()
        self.clock = clock
        self.subscribed: frozenset = frozenset()
        self.deadline: Optional[float] = None
        self.builds = 0  # parents built (tests: count the work)

    def rows(self, subscriptions: Iterable[str], states, now: Optional[float] = None) -> Dict[str, dict]:
        """Every subscribed parent's rows, from ``states`` (the published agent states)."""
        subs = frozenset(subscriptions or ())
        self.subscribed = subs
        self.deadline = None
        if not subs:
            self.files.forget_journals(())
            return {}
        now = self.clock() if now is None else now
        keep = self.keep_seconds()
        out: Dict[str, dict] = {}
        deadlines = []
        journals: set = set()
        for state in states:
            sid = getattr(state, "session_id", None)
            if sid not in subs:
                continue
            hook_state = self.swarm_state(state.name)
            if not isinstance(hook_state, dict):
                continue
            sw = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
            details = self.files.details(sw.get("transcript"))
            rows, deadline = swarm.virtual_rows(sid, hook_state, now, keep, details,
                                                self.conversations(sid))
            self.builds += 1
            out.update(rows)
            journals |= details.journal_paths
            if deadline is not None:
                deadlines.append(deadline)
        self.files.forget_journals(journals)
        self.deadline = min(deadlines) if deadlines else None
        return out

    def due(self, subscriptions: Iterable[str], now: Optional[float] = None) -> bool:
        """Whether the rows would change before the next tick builds them."""
        subs = frozenset(subscriptions or ())
        if subs != self.subscribed:
            return True
        if not subs:
            return False
        now = self.clock() if now is None else now
        if self.deadline is not None and now >= self.deadline:
            return True
        return self.files.journals_changed()
