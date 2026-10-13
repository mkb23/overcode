"""The engine between ticks: wake on a hook file change, not on a timer.

docs/design/engine-0.6.md, "The sensor path". While the daemon sleeps for
its loop interval it wakes in short steps: each step beats the watchdog,
listens for a view becoming visible or the TUI's activity signal, and scans
the agents' hook state files (``WakeScan``). Agents whose file changed are
re-detected and published at once by a quick tick, so a hook-driven status
change reaches views within a scan interval rather than the next tick.
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

from .paths import agent_path
from .settings import DAEMON, get_activity_signal_path

# Wake scan (docs/design/engine-0.6.md): between ticks the daemon stats each
# agent's hook state file this often and re-detects only agents whose file
# changed, so a hook-driven status change is published within about this
# long instead of waiting for the next tick.
WAKE_SCAN_ATTENDED_SECONDS = 0.25
WAKE_SCAN_UNATTENDED_SECONDS = 2.0
# Between full ticks the state file is rewritten at most this often; views
# on engine.sock get every change as it happens.
QUICK_TICK_STATE_SAVE_SECONDS = 1.0


# A Stop that lands within the hook detector's sticky-green window of the
# turn's last activity is read as still working until the window passes
# (#448). Nothing is written when it passes, so the wake scan re-detects an
# agent once, this long after its hook file last changed: the Stop shows
# when the window ends, not at the next full tick.
def sticky_recheck_seconds() -> float:
    from .hook_status_detector import _RECENT_ACTIVITY_WINDOW_SECONDS

    return _RECENT_ACTIVITY_WINDOW_SECONDS + 0.1


def check_activity_signal(session: str = None) -> bool:
    """Check for and consume the activity signal from TUI.

    Args:
        session: tmux session name (default: from config)
    """
    if session is None:
        session = DAEMON.default_tmux_session
    signal_path = get_activity_signal_path(session)
    # Atomic: just try to unlink, don't check exists() first (TOCTOU race)
    try:
        signal_path.unlink()
        return True
    except FileNotFoundError:
        # Signal doesn't exist - that's fine
        return False
    except OSError:
        # Other error (permissions, etc) - signal may exist but can't consume
        return False


class WakeScan:
    """Each agent's hook state file signature, and the sticky-green rechecks."""

    def __init__(self):
        # Each agent's hook state file signature, by overcode session id
        self.signatures: Dict[str, Optional[Tuple[int, int]]] = {}
        # Agents to re-detect once the sticky-green window has passed
        # (session id -> monotonic deadline)
        self.rechecks: Dict[str, float] = {}

    def changes(self, directory, states) -> set:
        """Session ids of agents whose hook state file changed since the last scan.

        One stat per live agent (``states``: the published agent states),
        of the file in its own directory (``agents/<id>/``, or the
        name-keyed file of an agent launched before those: paths.agent_path);
        an agent seen for the first time is recorded, not reported (the next
        full tick covers it). An agent whose file changed is reported once
        more when the detector's sticky-green window has passed
        (sticky_recheck_seconds).
        """
        changed = set()
        seen = set()
        rechecks = self.rechecks
        now = time.monotonic()
        for state in states:
            sid = state.session_id
            if not sid:
                continue
            seen.add(sid)
            path = agent_path("", sid, state.name, "hook_state", base=directory)
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
            except OSError:
                sig = None
            previous = self.signatures.get(sid, sig)
            self.signatures[sid] = sig
            if sig != previous:
                changed.add(sid)
                if self.hook_event(path) == "Stop":
                    rechecks[sid] = now + sticky_recheck_seconds()
                else:
                    rechecks.pop(sid, None)
            elif sid in rechecks and now >= rechecks[sid]:
                changed.add(sid)
                del rechecks[sid]
        for sid in [s for s in self.signatures if s not in seen]:
            del self.signatures[sid]
            rechecks.pop(sid, None)
        return changed

    @staticmethod
    def hook_event(path) -> Optional[str]:
        """The event a changed hook state file holds (one small read, on change only)."""
        try:
            with open(path) as f:
                event = json.load(f).get("event")
        except (OSError, ValueError, AttributeError):
            return None
        return event if isinstance(event, str) else None


class WakeLoop:
    """The daemon's sleep between ticks, and the quick ticks it runs.

    Drives the ``daemon`` it belongs to: its state, watchdog beat,
    engine.sock, detection and publishing.
    """

    def __init__(self, daemon):
        self.daemon = daemon
        self.scan = WakeScan()

    def changes(self) -> set:
        """Agents whose hook file changed since the last scan (``WakeScan.changes``)."""
        daemon = self.daemon
        return self.scan.changes(daemon.state_path.parent, daemon.state.sessions)

    def sleep(self, total_seconds: int) -> None:
        """Sleep with activity signal checking.

        The signal (a TUI keypress, or a TUI re-attaching) ends the sleep
        and puts the next loop on the fast interval, so an unattended
        daemon is back within a second of the user's return; a bare
        ``tmux attach`` with no TUI is seen by the next tick's listing.
        """
        daemon = self.daemon
        # Time is counted in slept seconds (not the wall clock), so the loop
        # is exact under a patched sleep and never oversleeps its interval.
        elapsed = 0.0
        last_scan = 0.0
        while not daemon._shutdown:
            remaining = total_seconds - elapsed
            if remaining <= 1e-9:
                return
            attended = daemon.state.interval_mode != "unattended"
            # Attended: wake every scan interval. Unattended: wake each
            # second for the activity signal (a returning person is seen
            # within a second), scan hooks every WAKE_SCAN_UNATTENDED_SECONDS.
            step = min(WAKE_SCAN_ATTENDED_SECONDS if attended else 1.0, remaining)
            time.sleep(step)
            elapsed += step
            daemon._beat()

            engine = daemon._engine
            if not attended and engine is not None and engine.attended:
                # A view on engine.sock is being looked at again: back to
                # the attended cadences now, not at the end of the long sleep
                daemon.log.info("A view became visible → waking up")
                return
            if check_activity_signal(daemon.tmux_session):
                daemon.log.info("User activity detected → waking up")
                daemon.state.current_interval = DAEMON.interval_fast
                daemon.state.interval_mode = "attended"
                daemon.state.save(daemon.state_path)
                return
            # Hook-driven changes publish now, not at the next tick
            if not attended and elapsed - last_scan < WAKE_SCAN_UNATTENDED_SECONDS:
                continue
            last_scan = elapsed
            changed = self.changes()
            if changed:
                try:
                    self.quick_tick(changed, datetime.now())
                except Exception as e:  # the full tick will retry
                    daemon.log.warn(f"quick tick failed: {e}")

    def quick_tick(self, ids: set, now: datetime) -> None:
        """Re-detect only the agents ``ids`` and publish, between full ticks."""
        daemon = self.daemon
        if daemon.detector.mode != "hooks":
            daemon._refresh_detection_mode()
        index = daemon._session_index()
        changed = [s for s in index.by_id.values()
                   if s.tmux_session == daemon.tmux_session and s.id in ids]
        if not changed:
            return
        try:
            daemon.recorder_store.apply_visits(daemon._engine)
            states, _ = daemon._detect_and_enrich(changed, now, index)
            fresh = {s.session_id: s for s in states}
            merged = [fresh.pop(s.session_id, s) for s in daemon.state.sessions]
            merged += list(fresh.values())
            # Over the whole fleet: _detect_and_enrich saw only the changed
            # agents, so a parent re-detected without its children got 0
            daemon._compute_subtree_costs(merged)
            save = time.monotonic() - daemon._last_state_save >= QUICK_TICK_STATE_SAVE_SECONDS
            daemon._publish_state(merged, save=save)
            # Hook-driven transitions land here: persist what they recorded
            # (a closed episode, a bell) now, not at the next full tick, so a
            # restart in between neither replays nor re-rings them. Only
            # when something changed (RecorderStore.save checks).
            daemon.recorder_store.save()
        finally:
            daemon._flush_pending_writes()
