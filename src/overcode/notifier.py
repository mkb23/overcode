"""
macOS notification integration for agent bells (#235).

Sends native notifications when agents transition to waiting_user state,
so users working in other apps can hear/see when agents need attention.
"""

import shutil
import subprocess
import sys
import time


class MacNotifier:
    """Coalescing macOS notifier for agent attention bells.

    Queues agent names during a status update cycle, then flushes a single
    coalesced notification at the end.  Uses terminal-notifier when available
    (supports grouping/replacement), falling back to osascript.
    """

    MODES = ("off", "sound", "banner", "both")

    def __init__(self, mode: str = "off", coalesce_seconds: float = 2.0):
        self.mode = mode if mode in self.MODES else "off"
        self.coalesce_seconds = coalesce_seconds
        self._pending: list[tuple[str, str | None]] = []  # (name, task)
        self._last_send: float = 0.0
        self._has_terminal_notifier: bool | None = None  # lazy-detected

    def queue(self, agent_name: str, task: str | None = None) -> None:
        """Queue an agent bell for the current cycle.  No-ops when off or non-darwin."""
        if self.mode == "off" or sys.platform != "darwin":
            return
        self._pending.append((agent_name, task))

    def flush(self) -> None:
        """Send a coalesced notification for all queued bells, then clear."""
        if not self._pending or self.mode == "off" or sys.platform != "darwin":
            self._pending.clear()
            return

        now = time.monotonic()
        if now - self._last_send < self.coalesce_seconds:
            # Too soon — hold until next cycle
            return

        names = [name for name, _ in self._pending]
        task = self._pending[0][1] if len(self._pending) == 1 else None

        subtitle, message = self._format(names, task)
        self._send(message, subtitle)

        self._last_send = now
        self._pending.clear()

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format(names: list[str], task: str | None) -> tuple[str | None, str]:
        """Return (subtitle, message) for the notification.

        subtitle is only set for single-agent + task (used by terminal-notifier).
        """
        if len(names) == 1:
            msg = f"{names[0]} needs attention"
            if task:
                return msg, task
            return None, msg
        elif len(names) == 2:
            return None, f"{names[0]} and {names[1]} need attention"
        elif len(names) == 3:
            return None, f"{names[0]}, {names[1]}, and {names[2]} need attention"
        else:
            others = len(names) - 2
            return None, f"{names[0]}, {names[1]}, and {others} others need attention"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _send(self, message: str, subtitle: str | None = None) -> None:
        """Fire-and-forget a macOS notification via best available backend."""
        want_sound = self.mode in ("sound", "both")
        want_banner = self.mode in ("banner", "both")

        if self._use_terminal_notifier():
            self._send_terminal_notifier(message, subtitle, want_sound, want_banner)
        else:
            self._send_osascript(message, subtitle, want_sound, want_banner)

    def _use_terminal_notifier(self) -> bool:
        if self._has_terminal_notifier is None:
            self._has_terminal_notifier = shutil.which("terminal-notifier") is not None
        return self._has_terminal_notifier

    def _send_terminal_notifier(
        self, message: str, subtitle: str | None,
        want_sound: bool, want_banner: bool,
    ) -> None:
        cmd = ["terminal-notifier", "-title", "Overcode", "-group", "overcode-bell"]
        if subtitle:
            cmd += ["-subtitle", subtitle]
        cmd += ["-message", message]
        if want_sound:
            cmd += ["-sound", "Hero"]
        if not want_banner:
            # terminal-notifier doesn't have a silent-banner flag;
            # skip entirely if only sound is wanted
            if not want_sound:
                return
            # sound-only: we still call terminal-notifier for the sound,
            # but use a minimal notification that auto-dismisses
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def _send_osascript(
        self, message: str, subtitle: str | None,
        want_sound: bool, want_banner: bool,
    ) -> None:
        if want_banner:
            display_text = f"{subtitle}\\n{message}" if subtitle else message
            script = f'display notification "{display_text}" with title "Overcode"'
            if want_sound:
                script += ' sound name "Hero"'
            try:
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
        elif want_sound:
            # Sound-only via afplay (no banner)
            try:
                subprocess.Popen(
                    ["afplay", "/System/Library/Sounds/Hero.aiff"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
