"""
presence_logger.py

Cross-platform presence logger that records user presence/absence stats.

Supports:
- macOS: Quartz APIs (CGEventSource, CGSession)
- Linux: DBus (org.freedesktop.ScreenSaver / org.gnome.ScreenSaver)
         with xprintidle fallback for idle time (#91)

Records once per SAMPLE_INTERVAL:
- timestamp (ISO8601 local time)
- state (1=locked/sleep, 2=screen on inactive, 3=screen on active)
- idle_seconds
- locked (0/1)
- inferred_sleep (0/1)

Data is appended to:
    ~/.overcode/presence_log.csv

Usage from another Python app:

    from overcode.presence_logger import start_background_logger

    logger = start_background_logger()
    # ... your app runs ...
    logger.stop()  # optional, logs until process exits anyway

CLI usage:

    overcode presence

to run it in the foreground.
"""

import csv
import datetime as dt
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .pid_utils import is_process_running, get_process_pid, write_pid_file, remove_pid_file

# Check for macOS-specific dependencies
try:
    from Quartz import (
        CGEventSourceSecondsSinceLastEventType,
        kCGEventSourceStateCombinedSessionState,
        kCGAnyInputEventType,
    )
    from ApplicationServices import CGSessionCopyCurrentDictionary
    MACOS_APIS_AVAILABLE = True
except ImportError:
    MACOS_APIS_AVAILABLE = False

# Check for Linux DBus support (#91)
LINUX_DBUS_AVAILABLE = False
LINUX_PLATFORM = platform.system() == "Linux"
_dbus_session_bus = None

if LINUX_PLATFORM:
    try:
        import dbus
        _dbus_session_bus = dbus.SessionBus()
        LINUX_DBUS_AVAILABLE = True
    except Exception:
        pass


# ---- config -----------------------------------------------------------------

OVERCODE_DIR = Path.home() / ".overcode"
PRESENCE_PID_FILE = OVERCODE_DIR / "presence.pid"
DEFAULT_SAMPLE_INTERVAL = 60  # seconds
DEFAULT_IDLE_THRESHOLD = 60   # seconds


def is_presence_running() -> bool:
    """Check if the presence logger process is currently running.

    Returns True if PID file exists and process is alive.
    """
    return is_process_running(PRESENCE_PID_FILE)


def get_presence_pid() -> Optional[int]:
    """Get the presence logger PID if running, None otherwise."""
    return get_process_pid(PRESENCE_PID_FILE)


def _write_pid_file() -> None:
    """Write current PID to file."""
    write_pid_file(PRESENCE_PID_FILE)


def _remove_pid_file() -> None:
    """Remove PID file."""
    remove_pid_file(PRESENCE_PID_FILE)


def default_log_path() -> str:
    """Return default CSV path under ~/.overcode/."""
    OVERCODE_DIR.mkdir(parents=True, exist_ok=True)
    return str(OVERCODE_DIR / "presence_log.csv")


@dataclass
class PresenceLoggerConfig:
    sample_interval: int = DEFAULT_SAMPLE_INTERVAL
    idle_threshold: int = DEFAULT_IDLE_THRESHOLD
    log_path: str = ""

    def __post_init__(self):
        if not self.log_path:
            self.log_path = default_log_path()


# ---- low-level state helpers -----------------------------------------------


def get_idle_seconds() -> float:
    """Seconds since last user input (mouse/keyboard) in current session.

    Supports macOS (Quartz) and Linux (DBus / xprintidle fallback).
    """
    if MACOS_APIS_AVAILABLE:
        return CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateCombinedSessionState,
            kCGAnyInputEventType,
        )

    if LINUX_PLATFORM:
        return _linux_get_idle_seconds()

    return 0.0


def is_screen_locked() -> bool:
    """Detect if the screen/session is locked.

    Supports macOS (CGSession) and Linux (DBus screensaver interfaces).
    """
    if MACOS_APIS_AVAILABLE:
        session_info = CGSessionCopyCurrentDictionary()
        if not session_info:
            return False
        if session_info.get("CGSSessionScreenIsLocked", 0):
            return True
        on_console = session_info.get("kCGSessionOnConsoleKey")
        if isinstance(on_console, bool) and not on_console:
            return True
        return False

    if LINUX_PLATFORM:
        return _linux_is_screen_locked()

    return False


# ---- Linux helpers (#91) ---------------------------------------------------


def _linux_get_idle_seconds() -> float:
    """Get idle time on Linux via DBus or xprintidle fallback."""
    # Try DBus ScreenSaver interface (GNOME, KDE, etc.)
    if LINUX_DBUS_AVAILABLE and _dbus_session_bus is not None:
        for service in (
            "org.freedesktop.ScreenSaver",
            "org.gnome.Mutter.IdleMonitor",
        ):
            try:
                if service == "org.gnome.Mutter.IdleMonitor":
                    obj = _dbus_session_bus.get_object(
                        service, "/org/gnome/Mutter/IdleMonitor/Core"
                    )
                    idle_ms = obj.GetIdletime(
                        dbus_interface="org.gnome.Mutter.IdleMonitor"
                    )
                else:
                    obj = _dbus_session_bus.get_object(
                        service, "/org/freedesktop/ScreenSaver"
                    )
                    idle_ms = obj.GetSessionIdleTime(
                        dbus_interface="org.freedesktop.ScreenSaver"
                    )
                return float(idle_ms) / 1000.0
            except Exception:
                continue

    # Fallback: xprintidle command
    if shutil.which("xprintidle"):
        try:
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return float(result.stdout.strip()) / 1000.0
        except Exception:
            pass

    return 0.0


def _linux_is_screen_locked() -> bool:
    """Detect screen lock on Linux via DBus."""
    if not LINUX_DBUS_AVAILABLE or _dbus_session_bus is None:
        return False

    # Try common screensaver/lock interfaces
    interfaces = [
        ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"),
        ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
    ]
    for service, path, iface in interfaces:
        try:
            obj = _dbus_session_bus.get_object(service, path)
            locked = obj.GetActive(dbus_interface=iface)
            return bool(locked)
        except Exception:
            continue

    return False


def infer_sleep(last_ts: Optional[dt.datetime],
                now: dt.datetime,
                sample_interval: int) -> bool:
    """
    Infer whether the machine likely slept between last_ts and now, based on
    a gap larger than ~2x the sample interval.
    """
    if last_ts is None:
        return False
    gap = (now - last_ts).total_seconds()
    return gap > 2 * sample_interval


def classify_state(locked: bool,
                   idle_seconds: float,
                   slept: bool,
                   idle_threshold: int) -> int:
    """
    Map low-level measures to 3 presence states:

    1: screen locked/hibernating
    2: screen on, inactive (idle > idle_threshold)
    3: screen on, active
    """
    if locked or slept:
        return 1
    elif idle_seconds > idle_threshold:
        return 2
    else:
        return 3


def state_to_name(state: int) -> str:
    """Convert state number to human-readable name."""
    return {
        1: "locked/sleep",
        2: "inactive",
        3: "active",
    }.get(state, "unknown")


# ---- main logger class ------------------------------------------------------


class PresenceLogger:
    """
    Background presence logger.

    - Call .start() to spin up a daemon thread that logs continuously.
    - Call .stop() to ask it to shut down cleanly.
    """

    def __init__(self, config: Optional[PresenceLoggerConfig] = None):
        self.config = config or PresenceLoggerConfig()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()  # protect start/stop
        self._last_state: Optional[int] = None

    def start(self) -> None:
        """Start the background logging thread (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="PresenceLoggerThread", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        """Request the logger to stop and optionally wait for it."""
        with self._lock:
            if self._thread is None:
                return
            self._stop_event.set()
            self._thread.join(timeout=timeout)
            # Don't reuse threads
            self._thread = None

    def get_current_state(self) -> tuple[int, float, bool]:
        """Get current presence state without logging.

        Returns:
            Tuple of (state, idle_seconds, is_locked)
        """
        idle = get_idle_seconds()
        locked = is_screen_locked()
        state = classify_state(
            locked=locked,
            idle_seconds=idle,
            slept=False,  # Can't infer sleep without history
            idle_threshold=self.config.idle_threshold,
        )
        return state, idle, locked

    def _run(self) -> None:
        """
        Main loop: every sample_interval seconds, log one row to CSV.

        CSV columns:
            timestamp_iso, state, idle_seconds, locked, inferred_sleep
        """
        cfg = self.config
        last_ts: Optional[dt.datetime] = None

        # Ensure directory exists
        os.makedirs(os.path.dirname(cfg.log_path), exist_ok=True)

        # Open in append mode; keep file handle for the process lifetime
        with open(cfg.log_path, "a", newline="") as f:
            writer = csv.writer(f)

            # Add header if file is empty
            try:
                if f.tell() == 0:
                    writer.writerow(
                        [
                            "timestamp",
                            "state",
                            "idle_seconds",
                            "locked",
                            "inferred_sleep",
                        ]
                    )
                    f.flush()
            except (OSError, IOError):
                # File write failed - header is not critical, continue
                pass

            while not self._stop_event.is_set():
                now = dt.datetime.now()
                slept = infer_sleep(last_ts, now, cfg.sample_interval)
                idle = get_idle_seconds()
                locked = is_screen_locked()
                state = classify_state(
                    locked=locked,
                    idle_seconds=idle,
                    slept=slept,
                    idle_threshold=cfg.idle_threshold,
                )

                self._last_state = state

                writer.writerow(
                    [
                        now.isoformat(),
                        state,
                        f"{idle:.1f}",
                        int(locked),
                        int(slept),
                    ]
                )
                f.flush()
                last_ts = now

                # Sleep in small chunks so stop() is responsive
                remaining = cfg.sample_interval
                while remaining > 0 and not self._stop_event.is_set():
                    step = min(1.0, remaining)
                    time.sleep(step)
                    remaining -= step


# ---- simple singleton helper for "just start it" ---------------------------

_singleton_logger: Optional[PresenceLogger] = None
_singleton_lock = threading.Lock()


def start_background_logger(
    sample_interval: int = DEFAULT_SAMPLE_INTERVAL,
    idle_threshold: int = DEFAULT_IDLE_THRESHOLD,
    log_path: Optional[str] = None,
) -> PresenceLogger:
    """
    Create (if needed) and start a global background PresenceLogger.

    Returns the logger instance, so you can call .stop() later if desired.

    Example:

        from overcode.presence_logger import start_background_logger

        logger = start_background_logger()
        # ... do stuff ...
        # logger.stop()
    """
    global _singleton_logger
    with _singleton_lock:
        if _singleton_logger is None:
            cfg = PresenceLoggerConfig(
                sample_interval=sample_interval,
                idle_threshold=idle_threshold,
                log_path=log_path or default_log_path(),
            )
            _singleton_logger = PresenceLogger(cfg)
            _singleton_logger.start()
        else:
            # Optionally you could update config here; for simplicity, we don't.
            _singleton_logger.start()
        return _singleton_logger


def get_singleton_logger() -> Optional[PresenceLogger]:
    """Get the singleton logger instance if it exists."""
    return _singleton_logger


def get_current_presence_state() -> tuple[int, float, bool]:
    """Get current presence state without needing a logger instance.

    Returns:
        Tuple of (state, idle_seconds, is_locked)
        state: 1=locked/sleep, 2=inactive, 3=active
    """
    idle = get_idle_seconds()
    locked = is_screen_locked()
    state = classify_state(
        locked=locked,
        idle_seconds=idle,
        slept=False,
        idle_threshold=DEFAULT_IDLE_THRESHOLD,
    )
    return state, idle, locked


def read_presence_history(hours: float = 3.0) -> list[tuple[dt.datetime, int]]:
    """Read presence history from CSV file.

    Args:
        hours: How many hours of history to read (default 3)

    Returns:
        List of (timestamp, state) tuples, oldest first
    """
    log_path = default_log_path()
    if not Path(log_path).exists():
        return []

    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    history = []

    try:
        with open(log_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = dt.datetime.fromisoformat(row['timestamp'])
                    if ts >= cutoff:
                        state = int(row['state'])
                        history.append((ts, state))
                except (ValueError, KeyError):
                    continue
    except (OSError, IOError):
        pass

    return history


# ---- CLI entrypoint ---------------------------------------------------------


def main() -> int:
    """
    Run the logger in the foreground; blocks until interrupted (Ctrl+C).

    Useful if you just want a standalone process:

        overcode presence
    """
    if not MACOS_APIS_AVAILABLE and not LINUX_PLATFORM:
        print("Error: No presence APIs available.")
        print("macOS: pip install pyobjc-framework-Quartz pyobjc-framework-ApplicationServices")
        print("Linux: pip install dbus-python (or install xprintidle)")
        return 1

    # Check if already running
    if is_presence_running():
        pid = get_presence_pid()
        print(f"Presence logger already running (PID: {pid})")
        return 1

    # Write PID file
    _write_pid_file()

    logger = PresenceLogger()
    logger.start()
    print(f"Presence logger running (PID: {os.getpid()})")
    print(f"Writing to: {logger.config.log_path}")
    print(f"Sample interval: {logger.config.sample_interval}s, idle threshold: {logger.config.idle_threshold}s")
    print("Press Ctrl+C to stop.")
    print()

    try:
        while True:
            state, idle, locked = logger.get_current_state()
            state_name = state_to_name(state)
            status = f"State: {state} ({state_name}), Idle: {idle:.0f}s, Locked: {locked}"
            print(f"\r{status:<60}", end="", flush=True)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping logger...")
        logger.stop()
    finally:
        _remove_pid_file()

    return 0


if __name__ == "__main__":
    sys.exit(main())
