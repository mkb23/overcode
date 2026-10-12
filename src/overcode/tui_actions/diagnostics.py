"""
TUI diagnostics: the event-loop heartbeat probe and the status-change log.

The probe records the event loop's tick-to-tick delay every 100 ms, plus
named event markers around the main-thread applies, to
event_loop_timing.csv (#465: on by default, hard-capped). The status-change
log (opt-in) records every per-agent status transition.
"""

from __future__ import annotations

import time
from datetime import datetime

# Event-loop heartbeat probe: the 5 s flush normally drains ~55 rows, so this
# only bites if the flush timer never runs. Without it the buffer grew for the
# life of the process (~10 rows/s => >100 MB after 15 h) whenever the probe
# was disabled, because _mark_event kept appending with no flush scheduled.
HEARTBEAT_LOG_MAX_ENTRIES = 10_000


class DiagnosticsMixin:
    """Mixin providing the heartbeat probe and status-change log for SupervisorTUI."""

    def _record_heartbeat(self) -> None:
        """Record one heartbeat tick. Runs every 100ms on the event loop."""
        if not getattr(self, "_heartbeat_enabled", False):
            return
        now = time.monotonic()
        if self._heartbeat_last > 0:
            delta_ms = (now - self._heartbeat_last) * 1000.0
            iso_ts = datetime.now().isoformat(timespec="milliseconds")
            self._append_heartbeat((iso_ts, f"{delta_ms:.1f}", ""))
        self._heartbeat_last = now

    def _mark_event(self, name: str) -> None:
        """Record a named event marker in the heartbeat log.

        A no-op when the probe is disabled: the flush timer only exists when
        it is enabled, so appending here would grow the buffer unbounded.
        """
        if not getattr(self, "_heartbeat_enabled", False):
            return
        now = time.monotonic()
        delta_ms = (now - self._heartbeat_last) * 1000.0 if self._heartbeat_last > 0 else 0.0
        iso_ts = datetime.now().isoformat(timespec="milliseconds")
        self._append_heartbeat((iso_ts, f"{delta_ms:.1f}", name))
        self._heartbeat_last = now

    def _append_heartbeat(self, row: tuple) -> None:
        """Buffer one probe row, dropping the oldest half at the cap (backstop)."""
        log = self._heartbeat_log
        if len(log) >= HEARTBEAT_LOG_MAX_ENTRIES:
            del log[: len(log) // 2]
        log.append(row)

    def _flush_heartbeat(self) -> None:
        """Write buffered heartbeat data to CSV. Runs every 5s."""
        if not self._heartbeat_log:
            return
        buf = self._heartbeat_log
        self._heartbeat_log = []
        try:
            path = self._heartbeat_csv_path
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with open(path, "a") as f:
                if write_header:
                    f.write("timestamp,delta_ms,event\n")
                for ts, delta, event in buf:
                    f.write(f"{ts},{delta},{event}\n")
            # Diagnostic-only file — hard cap as a backstop (#465). getattr
            # default covers lightweight test doubles built via __new__.
            from ..settings import cap_diagnostics_csv
            cap_diagnostics_csv(path, getattr(self, "_heartbeat_cap_mb", 100.0))
        except OSError:
            pass  # Best effort

    def _log_status_change(self, agent_name: str, old_status: str, new_status: str,
                           source: str, focused: bool, content_changed: bool = False) -> None:
        """Record a status change event for diagnostics."""
        iso_ts = datetime.now().isoformat(timespec="milliseconds")
        self._status_change_log.append(
            (iso_ts, agent_name, old_status, new_status, source, "Y" if focused else "N", "Y" if content_changed else "N")
        )

    def _flush_status_changes(self) -> None:
        """Write buffered status change data to CSV."""
        if not self._status_change_log:
            return
        buf = self._status_change_log
        self._status_change_log = []
        try:
            path = self._status_change_csv_path
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with open(path, "a") as f:
                if write_header:
                    f.write("timestamp,agent,old_status,new_status,source,focused,content_changed\n")
                for row in buf:
                    f.write(",".join(row) + "\n")
            # Same diagnostic-only backstop as event_loop_timing.csv (#465):
            # opt-in, but once on it is append-only for the life of the TUI.
            from ..settings import cap_diagnostics_csv
            cap_diagnostics_csv(path, getattr(self, "_heartbeat_cap_mb", 100.0))
        except OSError:
            pass
