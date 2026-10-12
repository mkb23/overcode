"""
Unit test configuration for Overcode.

This module provides fixtures for unit tests that need isolated state directories.
"""

import os
import subprocess
import pytest
import tempfile
import shutil
from pathlib import Path

# tests/conftest.py already pointed OVERCODE_DIR / OVERCODE_STATE_DIR /
# OVERCODE_TMUX_SOCKET at a per-run temp dir. Unit tests go further, as a
# backstop for code (or tests) that ignore those variables or clear every
# OVERCODE_* variable: HOME and every tool home overcode reads move into
# the run dir, and bare `tmux` calls reach a private server, never the
# user's. Done at import, before test modules import overcode, so
# paths built from HOME resolve inside the run dir too.
RUN_DIR = Path(os.environ["OVERCODE_DIR"]).parent
REAL_HOME = Path.home()
_FAKE_HOME = RUN_DIR / "home"
_FAKE_HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(_FAKE_HOME)
for _var in ("CODEX_HOME", "GROK_HOME", "HERMES_HOME", "OPENCODE_DATA_DIR",
             "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
             "XDG_CACHE_HOME", "XDG_STATE_HOME"):
    os.environ.pop(_var, None)
# A short path: tmux's socket lives at $TMUX_TMPDIR/tmux-<uid>/<name>, and
# macOS caps AF_UNIX paths at 104 bytes.
_TMUX_TMPDIR = tempfile.mkdtemp(prefix="ocpt-", dir="/tmp")
os.environ["TMUX_TMPDIR"] = _TMUX_TMPDIR

# Long-lived overcode processes a unit test must never start for real.
_DAEMON_MODULES = (
    "overcode.monitor_daemon",
    "overcode.supervisor_daemon",
    "overcode.web_server_runner",
    "overcode.presence_logger",
)
_blocked_spawns: list[str] = []
_real_popen_init = subprocess.Popen.__init__


def _guarded_popen_init(self, args, *a, **kw):
    """Refuse to start a real overcode daemon from a unit test.

    The no_real_daemons fixture stubs spawn_daemon; this catches every
    other route (a direct Popen, a module that bound spawn_daemon before
    the stub, a CLI command run in-process). The test fails at the end of
    the run even if product code swallows the error.
    """
    if isinstance(args, (str, bytes, os.PathLike)):
        tokens = os.fsdecode(args).split()
    else:
        tokens = [os.fsdecode(x) if isinstance(x, (bytes, os.PathLike)) else str(x)
                  for x in args]
    module = next((tokens[i + 1] for i, tok in enumerate(tokens[:-1])
                   if tok == "-m" and tokens[i + 1] in _DAEMON_MODULES), None)
    if module is not None:
        current = os.environ.get("PYTEST_CURRENT_TEST", "?")
        _blocked_spawns.append(f"{current}: {' '.join(tokens)}")
        raise PermissionError(
            f"unit tests must not start {module} (tests/unit/conftest.py); "
            "patch overcode.pid_utils.spawn_daemon or the caller instead"
        )
    _real_popen_init(self, args, *a, **kw)


subprocess.Popen.__init__ = _guarded_popen_init

# Model-metadata lookups resolve against the freshest models.dev catalog on
# the machine (~/.overcode/cache, opencode's cache) — tests must see the
# bundled snapshot only, so results don't depend on what the developer's
# host happens to have fetched. Tests of the local tiers delenv this.
os.environ.setdefault("OVERCODE_MODEL_METADATA_BUNDLED_ONLY", "1")


@pytest.fixture(autouse=True)
def no_real_daemons(monkeypatch):
    """Unit tests never start real daemons (#485).

    Mounting SupervisorTUI spawns a monitor daemon. The pilot tests finish
    before it writes its PID file, so a cleanup by PID file can miss it and
    it outlives the run. Tests that check the spawn patch it
    themselves, over this stub.
    """
    import overcode.pid_utils
    import overcode.tui

    def fake_spawn_daemon(args):
        return None

    monkeypatch.setattr(overcode.pid_utils, "spawn_daemon", fake_spawn_daemon)
    monkeypatch.setattr(overcode.tui, "spawn_daemon", fake_spawn_daemon)


@pytest.fixture(autouse=True)
def no_real_skill_profiles(monkeypatch):
    """Unit tests never read or write the real skills config (#499).

    Launching resolves a skill profile from config.yaml (folder pins, the
    default profile), so a developer's own pins would leak into launcher
    tests. Tests of the config itself use the ``skills_config`` fixture in
    test_skill_library.py, which patches over this.
    """
    import overcode.skill_library
    store: dict = {}
    monkeypatch.setattr(overcode.skill_library, "_skills_config", lambda: dict(store))
    monkeypatch.setattr(overcode.skill_library, "_save_skills_config",
                        lambda section: (store.clear(), store.update(section)))


@pytest.fixture(autouse=True)
def activity_log_in_tmp(monkeypatch, tmp_path_factory):
    """Unit tests never write the real usage log (#483).

    OVERCODE_ACTIVITY=0 (tests/conftest.py) is not enough on its own: some
    tests clear every OVERCODE_* variable. Tests that check the log's
    location patch get_activity_dir themselves, over this.
    """
    import overcode.activity_log
    import overcode.mentor
    d = tmp_path_factory.mktemp("activity")
    monkeypatch.setattr(overcode.activity_log, "get_activity_dir", lambda: d)
    # Mounting the TUI loads (and on first sight writes) the mentor's state.
    monkeypatch.setattr(overcode.mentor, "state_path", lambda: d / "journey_state.json")


@pytest.fixture(autouse=True)
def fresh_opencode_window_indexes():
    """Each test starts with no published opencode window index (#517).

    The readers share module-level indexes, keyed by the store's path, that
    burn-rate calls reuse for up to 10 s. A test that rewrites its store and
    reads the window again inside that age would otherwise see the earlier
    scan. Tests of the reuse itself publish their own indexes.
    """
    from overcode.backends import opencode_stats

    opencode_stats.clear_window_indexes()
    yield
    opencode_stats.clear_window_indexes()


@pytest.fixture(autouse=True)
def default_keymap_only(monkeypatch):
    """Unit tests run on the built-in keys (#510).

    A developer's own `keys:` config (preset, overrides) would otherwise
    change which key a pilot test's press reaches. Tests of the config
    patch user_keys_config themselves, over this. The active keymap a
    mounted TUI sets is cleared afterwards so it can't leak between tests.
    """
    import overcode.keymap
    monkeypatch.setattr(overcode.keymap, "user_keys_config", lambda: {})
    yield
    overcode.keymap.set_active(None)


def _daemon_children() -> list[str]:
    """This process's child daemons, as "pid command" lines.

    spawn_daemon's reaper thread keeps a daemon our child while we run, so
    this only sees daemons this test run started.
    """
    import subprocess
    out = subprocess.run(
        ["pgrep", "-lf", "-P", str(os.getpid()), r"overcode\.(monitor|supervisor)_daemon"],
        capture_output=True, text=True,
    ).stdout
    return out.splitlines()


def _run_dir_daemons() -> list[str]:
    """overcode daemons anywhere on the machine whose environment names this run's dir.

    Catches what _daemon_children can't: a daemon that double-forked or was
    reparented to launchd, or one a CLI subprocess started.
    """
    out = subprocess.run(
        ["pgrep", "-f", r"overcode\.(monitor_daemon|supervisor_daemon|web_server_runner|presence_logger)"],
        capture_output=True, text=True,
    ).stdout
    found = []
    for pid in out.split():
        env = subprocess.run(["ps", "eww", "-o", "command=", "-p", pid],
                             capture_output=True, text=True).stdout
        if str(RUN_DIR) in env or _TMUX_TMPDIR in env:
            found.append(f"{pid} {env.split(' OVERCODE_', 1)[0][:200]}")
    return found


def _mock_paths() -> list[str]:
    """State paths built from a Mock or an argv flag instead of a session name.

    MagicMock's default __fspath__ is "MagicMock/<name>/<id>", so a Mock
    passed as a session name lands as a MagicMock/ directory; an argv flag
    taken as a session name lands as "--flag". Either is a test bug.
    """
    bad = []
    for root in (RUN_DIR / "overcode", _FAKE_HOME):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_dir() and (p.name.startswith(("MagicMock", "Mock", "NonCallableMagicMock",
                                                  "<MagicMock", "<Mock", "-"))):
                bad.append(str(p.relative_to(RUN_DIR)))
    return bad


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if a unit test leaked (#485).

    Leaks are: a real daemon left running, a daemon spawn the Popen guard
    refused, or a state directory named after a Mock or an argv flag.
    """
    import signal
    problems = []
    leaked = sorted(set(_daemon_children()) | set(_run_dir_daemons()))
    for line in leaked:
        try:
            os.kill(int(line.split()[0]), signal.SIGTERM)
        except (ValueError, OSError):
            pass
    if leaked:
        problems.append("Unit tests started real daemons (killed now):\n  " + "\n  ".join(leaked))
    if _blocked_spawns:
        problems.append("Unit tests tried to start real daemons (refused):\n  "
                        + "\n  ".join(_blocked_spawns))
    mock_paths = _mock_paths()
    if mock_paths:
        problems.append("Unit tests built state paths from a Mock or an argv flag:\n  "
                        + "\n  ".join(mock_paths))
    # Every tmux server the run started lives under _TMUX_TMPDIR (by -L name
    # or the default socket); stop them all, with whatever they run.
    for sock in Path(_TMUX_TMPDIR).glob("tmux-*/*"):
        try:
            subprocess.run(["tmux", "-S", str(sock), "kill-server"],
                           capture_output=True, timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
    # Unset first: a later tmux call (tests/conftest.py's pytest_unconfigure)
    # would otherwise recreate the directory.
    os.environ.pop("TMUX_TMPDIR", None)
    shutil.rmtree(_TMUX_TMPDIR, ignore_errors=True)
    if problems:
        print("\n\n" + "\n\n".join(problems))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


# ── The TUI pilot harness (tests/tui_harness.py) ─────────────────────────


@pytest.fixture
def tui_state_dir(monkeypatch):
    """A short private OVERCODE_DIR for one TUI test.

    Short because the engine's Unix socket lives under it and macOS caps
    AF_UNIX paths at 104 bytes (tmp_path is too long there).
    """
    d = Path(tempfile.mkdtemp(prefix="oc-tui-", dir="/tmp"))
    monkeypatch.setenv("OVERCODE_DIR", str(d))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(d / "s"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def make_tui(tui_state_dir, monkeypatch):
    """Build a SupervisorTUI for a pilot test: ``make_tui(sessions=..., engine=..., ...)``.

    - ``sessions``: the agents ``list_sessions`` returns (a list, or a
      callable returning one); None reads the private sessions.json.
    - ``engine``: a ``FakeEngine`` (default: a new one, connected from
      mount with an empty snapshot), or ``"socket"`` for the real
      EngineClient (the test serves the socket).
    - ``timers``: False (default) starts no periodic timer; call the ticks.
    - ``size``: the default terminal size for ``app.run_test()`` (else Textual's 80x24).

    The app carries ``app.engine`` (the FakeEngine, or None),
    ``app.daemon_starts`` (the stubbed ``_ensure_monitor_daemon``) and a
    MagicMock ``_notifier``. Status detection raises: the engine does it.
    """
    from functools import partial
    from unittest.mock import MagicMock

    from overcode.launcher import AgentLauncher
    from overcode.status_detector_factory import StatusDetectorDispatcher
    import overcode.tui_engine_link
    from overcode.tui import SupervisorTUI
    from tests.tui_harness import FakeEngine

    def no_detection(*a, **kw):
        raise AssertionError("the TUI must not detect status (the engine does)")

    def make(sessions=None, *, engine=None, timers=False, size=None,
             tmux_session="test", **kwargs):
        starts = MagicMock()
        monkeypatch.setattr(SupervisorTUI, "_ensure_monitor_daemon", starts)
        monkeypatch.setattr(StatusDetectorDispatcher, "detect_status", no_detection)
        if sessions is not None:
            source = sessions if callable(sessions) else (lambda: list(sessions))
            monkeypatch.setattr(AgentLauncher, "list_sessions", lambda self: source())
        if engine != "socket":
            engine = engine if engine is not None else FakeEngine()
            monkeypatch.setattr(overcode.tui_engine_link, "EngineClient", engine.client_factory)
        app = SupervisorTUI(tmux_session=tmux_session, timers=timers, **kwargs)
        # Recorded, never sent (its mode stays the real one's)
        app._notifier = MagicMock(mode=app._notifier.mode)
        app.daemon_starts = starts
        app.engine = engine if engine != "socket" else None
        if size is not None:
            app.run_test = partial(app.run_test, size=size)
        return app

    return make


@pytest.fixture
def tui(make_tui):
    """The usual pilot app: alpha, bravo and charlie, a connected engine, no timers."""
    from tests.tui_harness import three_agents

    return make_tui(three_agents())


@pytest.fixture(autouse=True)
def _launcher_waits_are_instant(monkeypatch):
    """Key-press delays and graceful-exit waits sleep for real in production;
    in unit tests they would add ~0.5 s per rename or restart."""
    from overcode.launcher import AgentLauncher

    monkeypatch.setattr(AgentLauncher, "sleep", staticmethod(lambda seconds: None))


@pytest.fixture(autouse=True)
def _stop_presence_loggers(monkeypatch):
    """Stop every presence logger a test started.

    Tests that build a real MonitorDaemon start its PresenceLogger thread and
    never stop it. Dozens leaked across a run; on a two-core CI runner they
    starved slow tests into the 120 s timeout.
    """
    from overcode import presence_logger

    started = []
    original = presence_logger.PresenceLogger.start

    def start(self):
        started.append(self)
        return original(self)

    monkeypatch.setattr(presence_logger.PresenceLogger, "start", start)
    yield
    for logger in started:
        try:
            logger.stop(timeout=1.0)
        except Exception:
            pass
