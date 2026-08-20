"""Per-session status detection dispatch (Phase 3).

Covers the three seams introduced with the backend-aware detector stack:
  1. ``get_patterns(backend_name)`` — each backend supplies its own chrome.
  2. ``resolve_session_detection_mode`` — per-agent override → legacy
     per-agent opt-out → backend capability → fleet default.
  3. ``StatusDetectorDispatcher`` — a mixed fleet resolves correctly inside
     one tick, each session scraped with its own backend's patterns.
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import DEFAULT_BACKEND, register_backend, unregister_backend
from overcode.interfaces import MockTmux
from overcode.status_constants import (
    STATUS_RUNNING,
    STATUS_WAITING_USER,
)
from overcode.status_detector import PollingStatusDetector
from overcode.status_detector_factory import (
    StatusDetectorDispatcher,
    resolve_session_detection_mode,
)
from overcode.status_patterns import DEFAULT_PATTERNS, get_patterns
from tests.fixtures import create_mock_session
from tests.unit.backend_doubles import HookedTestBackend, PollingOnlyBackend


ALT_IDLE_PANE = """
▸ Done with that.

»
  ? for help
"""

ALT_BUSY_PANE = """
▸ Fetching docs/spec.md

  ◍ working
  ctrl+c to stop
"""


@pytest.fixture
def hooked_backend():
    register_backend(HookedTestBackend())
    try:
        yield HookedTestBackend.name
    finally:
        unregister_backend(HookedTestBackend.name)


@pytest.fixture
def polling_only_backend():
    register_backend(PollingOnlyBackend())
    try:
        yield PollingOnlyBackend.name
    finally:
        unregister_backend(PollingOnlyBackend.name)


def _session(name, window, backend=DEFAULT_BACKEND, **kwargs):
    session = create_mock_session(name=name, tmux_window=window)
    session.backend = backend
    session.detection_mode_override = None
    for key, value in kwargs.items():
        setattr(session, key, value)
    return session


class TestPatternsRegistry:
    def test_default_is_claude_patterns(self):
        assert get_patterns() is DEFAULT_PATTERNS
        assert get_patterns("claude-code") is DEFAULT_PATTERNS

    def test_backend_supplies_its_own_patterns(self, hooked_backend):
        patterns = get_patterns(hooked_backend)
        assert patterns is not DEFAULT_PATTERNS
        assert patterns.prompt_chars == ["»"]

    def test_unknown_backend_falls_back_to_default(self):
        assert get_patterns("__never_registered__") is DEFAULT_PATTERNS

    def test_re_registering_invalidates_the_cache(self, hooked_backend):
        first = get_patterns(hooked_backend)

        class Rebranded(HookedTestBackend):
            def status_patterns(self):
                from overcode.status_patterns import StatusPatterns
                return StatusPatterns(prompt_chars=["→"])

        register_backend(Rebranded())
        second = get_patterns(hooked_backend)

        assert second is not first
        assert second.prompt_chars == ["→"]


class TestAltBackendPolling:
    """The polling detector reads all Claude-specific chrome from patterns."""

    def _detector(self, content, patterns):
        tmux = MockTmux()
        tmux.new_session("agents")
        tmux.sessions["agents"][1] = content
        return PollingStatusDetector("agents", tmux=tmux, patterns=patterns)

    def test_alt_prompt_is_waiting_user(self, hooked_backend):
        detector = self._detector(ALT_IDLE_PANE, get_patterns(hooked_backend))
        status, _, _ = detector.detect_status(_session("a", 1))
        assert status == STATUS_WAITING_USER

    def test_alt_busy_marker_is_running(self, hooked_backend):
        detector = self._detector(ALT_BUSY_PANE, get_patterns(hooked_backend))
        status, _, _ = detector.detect_status(_session("a", 1))
        assert status == STATUS_RUNNING

    def test_alt_permission_prompt_is_waiting_user(self, hooked_backend):
        pane = "▸ ls -la\n\n  Permission required\n  Allow once / Reject\n"
        detector = self._detector(pane, get_patterns(hooked_backend))
        status, activity, _ = detector.detect_status(_session("a", 1))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    def test_alt_status_bar_counts(self, hooked_backend):
        from overcode.status_patterns import extract_from_pane

        pane = "▸ working\n▤ auto-run on · 3 jobs · 2 workers · 1 watcher\n"
        extraction = extract_from_pane(pane, get_patterns(hooked_backend))

        assert extraction.background_bash_count == 3
        assert extraction.live_subagent_count == 2
        assert extraction.active_monitor_count == 1
        assert extraction.auto_accept_mode is True

    def test_claude_patterns_ignore_alt_chrome(self, hooked_backend):
        from overcode.status_patterns import extract_from_pane

        pane = "▸ working\n▤ auto-run on · 3 jobs · 2 workers · 1 watcher\n"
        extraction = extract_from_pane(pane)

        assert extraction.background_bash_count == 0
        assert extraction.live_subagent_count == 0
        assert extraction.auto_accept_mode is False


class TestResolveSessionDetectionMode:
    def test_falls_back_to_fleet_default(self):
        assert resolve_session_detection_mode(_session("a", 1), "hooks") == "hooks"
        assert resolve_session_detection_mode(_session("a", 1), "polling") == "polling"

    def test_per_agent_override_wins(self):
        session = _session("a", 1, detection_mode_override="polling")
        assert resolve_session_detection_mode(session, "hooks") == "polling"

        session = _session("a", 1, detection_mode_override="hooks")
        assert resolve_session_detection_mode(session, "polling") == "hooks"

    def test_legacy_opt_out_forces_polling(self):
        session = _session("a", 1, hook_status_detection=False)
        assert resolve_session_detection_mode(session, "hooks") == "polling"

    def test_override_beats_legacy_opt_out(self):
        session = _session(
            "a", 1, hook_status_detection=False, detection_mode_override="hooks"
        )
        assert resolve_session_detection_mode(session, "polling") == "hooks"

    def test_backend_without_hook_events_forces_polling(self, polling_only_backend):
        session = _session("a", 1, backend=polling_only_backend)
        assert resolve_session_detection_mode(session, "hooks") == "polling"

    def test_unknown_backend_forces_polling(self):
        session = _session("a", 1, backend="__never_registered__")
        assert resolve_session_detection_mode(session, "hooks") == "polling"

    def test_garbage_fleet_mode_is_polling(self):
        assert resolve_session_detection_mode(_session("a", 1), "nonsense") == "polling"

    def test_legacy_global_mode_file_is_the_fleet_default(self, tmp_path, monkeypatch):
        """Migration: the pre-Phase-3 detection_mode file still sets the default."""
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        from overcode.settings import resolve_detection_mode, write_detection_mode

        write_detection_mode("agents", "hooks")
        fleet_mode = resolve_detection_mode("agents")

        assert fleet_mode == "hooks"
        # An agent with no override inherits it; an override still wins.
        assert resolve_session_detection_mode(_session("a", 1), fleet_mode) == "hooks"
        overridden = _session("b", 2, detection_mode_override="polling")
        assert resolve_session_detection_mode(overridden, fleet_mode) == "polling"


class TestMixedFleetDispatch:
    """Two agents, one tick, different detection modes."""

    def _dispatcher(self, tmp_path, monkeypatch, fleet_mode="polling"):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        tmux = MockTmux()
        tmux.new_session("agents")
        idle_pane = "\n⏺ All done.\n\n>\n  ? for shortcuts\n"
        tmux.sessions["agents"][1] = idle_pane
        tmux.sessions["agents"][2] = idle_pane
        return StatusDetectorDispatcher("agents", tmux=tmux, mode=fleet_mode), tmux

    def _write_hook_state(self, tmp_path, session_name, event):
        state_dir = tmp_path / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"hook_state_{session_name}.json").write_text(
            json.dumps({"event": event, "timestamp": time.time(), "tool_name": "Bash"})
        )

    def test_hooks_and_polling_agents_in_one_tick(self, tmp_path, monkeypatch):
        dispatcher, _tmux = self._dispatcher(tmp_path, monkeypatch)
        self._write_hook_state(tmp_path, "hooked", "PreToolUse")

        hooked = _session("hooked", 1, detection_mode_override="hooks")
        polled = _session("polled", 2, detection_mode_override="polling")

        hooked_status, _, _ = dispatcher.detect_status(hooked)
        polled_status, _, _ = dispatcher.detect_status(polled)

        assert dispatcher.resolve_mode(hooked) == "hooks"
        assert dispatcher.resolve_mode(polled) == "polling"
        assert hooked_status == STATUS_RUNNING
        assert polled_status == STATUS_WAITING_USER

    def test_hook_agent_ignores_pane_state(self, tmp_path, monkeypatch):
        """The hooks agent's status comes from the file, not the idle pane."""
        dispatcher, _tmux = self._dispatcher(tmp_path, monkeypatch, fleet_mode="hooks")
        self._write_hook_state(tmp_path, "hooked", "Stop")

        hooked = _session("hooked", 1)
        status, activity, _ = dispatcher.detect_status(hooked)

        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_detector_pairs_are_per_backend(self, tmp_path, monkeypatch, hooked_backend):
        dispatcher, _tmux = self._dispatcher(tmp_path, monkeypatch)

        claude_session = _session("claude-agent", 1)
        alt_session = _session("alt-agent", 2, backend=hooked_backend)

        dispatcher.detect_status(claude_session)
        dispatcher.detect_status(alt_session)

        claude_polling, _ = dispatcher._pair_for(DEFAULT_BACKEND)
        alt_polling, _ = dispatcher._pair_for(hooked_backend)

        assert claude_polling is dispatcher.polling
        assert alt_polling is not dispatcher.polling
        assert alt_polling.patterns is get_patterns(hooked_backend)

    def test_capture_lines_applies_to_every_pair(self, tmp_path, monkeypatch, hooked_backend):
        dispatcher, _tmux = self._dispatcher(tmp_path, monkeypatch)
        dispatcher.detect_status(_session("alt-agent", 2, backend=hooked_backend))

        dispatcher.capture_lines = 123

        for polling, hooks in dispatcher._pairs.values():
            assert polling.capture_lines == 123
            assert hooks.capture_lines == 123

    def test_backend_without_hook_events_stays_on_polling(
        self, tmp_path, monkeypatch, polling_only_backend
    ):
        dispatcher, _tmux = self._dispatcher(tmp_path, monkeypatch, fleet_mode="hooks")
        self._write_hook_state(tmp_path, "nohooks", "PreToolUse")

        session = _session("nohooks", 1, backend=polling_only_backend)
        status, _, _ = dispatcher.detect_status(session)

        assert dispatcher.resolve_mode(session) == "polling"
        assert status == STATUS_WAITING_USER
