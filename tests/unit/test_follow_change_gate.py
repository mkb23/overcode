"""The follower captures its pane only when it may have changed (#528, audit R16).

Each follower used to capture 500 lines twice a second whatever the pane
did. ``ChangeGatedCapture`` probes the pane at the same rate with one
``display-message`` and runs the capture only when the probe says the pane
may have moved since the last capture, so nothing that follow would print
is seen any later.
"""

import os
import shutil
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from overcode import follow_mode
from overcode.follow_mode import UNCHANGED, ChangeGatedCapture, _probe_pane


@pytest.fixture
def gate(monkeypatch):
    """A gate over scripted probes, a counting capture and a settable clock."""
    state = {"probes": iter(()), "wall": 1000.5, "mono": 0.0, "captures": []}

    def capture(session, window):
        state["captures"].append(window)
        return f"text {len(state['captures'])}"

    monkeypatch.setattr(follow_mode, "_probe_pane", lambda s, w: next(state["probes"]))
    monkeypatch.setattr(follow_mode, "_capture_pane", capture)
    monkeypatch.setattr(follow_mode, "_wall_clock", lambda: state["wall"])
    monkeypatch.setattr(follow_mode, "_monotonic", lambda: state["mono"])

    def make(probes, keepalive=5.0):
        state["probes"] = iter(probes)
        return ChangeGatedCapture(keepalive_seconds=keepalive)

    make.state = state
    return make


class TestChangeGatedCapture:

    def test_first_poll_captures(self, gate):
        g = gate([(999, ("a",))])
        assert g.capture("agents", "w") == "text 1"
        assert gate.state["captures"] == ["w"]

    def test_a_pane_quiet_since_before_the_capture_is_not_captured_again(self, gate):
        g = gate([(999, ("a",))] * 3)
        g.capture("agents", "w")
        gate.state["wall"] += 0.5
        gate.state["mono"] += 0.5
        assert g.capture("agents", "w") is UNCHANGED
        assert g.capture("agents", "w") is UNCHANGED
        assert len(gate.state["captures"]) == 1 and g.skipped == 2

    def test_activity_in_the_capture_second_keeps_capturing(self, gate):
        """Output stamped in the second a capture ran may have come after it."""
        g = gate([(1000, ("a",))] * 4)
        g.capture("agents", "w")                 # started 1000.5, activity 1000
        gate.state["wall"] = 1000.9
        assert g.capture("agents", "w") == "text 2"
        gate.state["wall"] = 1001.2              # started after second 1000 ended
        assert g.capture("agents", "w") == "text 3"
        gate.state["wall"] = 1001.7
        assert g.capture("agents", "w") is UNCHANGED

    def test_an_in_place_redraw_moves_the_probe(self, gate):
        """A spinner leaves history and cursor alone but stamps the activity."""
        g = gate([(990, ("a", "990")), (1001, ("a", "1001"))])
        g.capture("agents", "w")
        gate.state["wall"] = 1001.5
        assert g.capture("agents", "w") == "text 2"

    def test_without_a_probe_answer_every_poll_captures(self, gate):
        g = gate([None, None])
        g.capture("agents", "w")
        g.capture("agents", "w")
        assert len(gate.state["captures"]) == 2

    def test_keepalive_captures_an_unchanged_pane(self, gate):
        g = gate([(999, ("a",))] * 2)
        g.capture("agents", "w")
        gate.state["mono"] += 5.0
        assert g.capture("agents", "w") == "text 2"


class TestProbe:

    def test_refuses_a_target_tmux_resolved_to_another_window(self):
        """tmux 3.7 answers a missing `session:=name` with some other window."""
        result = MagicMock(returncode=0, stdout="agents\tother\t3\t1000\t5\t0\t1\t80\t24\n")
        with patch("overcode.follow_mode.subprocess.run", return_value=result):
            assert _probe_pane("agents", "mine") is None
            assert _probe_pane("agents", "other")[0] == 1000
            assert _probe_pane("agents", "3")[0] == 1000  # legacy index target
            assert _probe_pane("elsewhere", "other") is None

    def test_no_answer_is_none(self):
        with patch("overcode.follow_mode.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="")):
            assert _probe_pane("agents", "w") is None
        with patch("overcode.follow_mode.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="\n")):
            assert _probe_pane("agents", "w") is None


class TestFollowLoopWithTheGate:

    def test_an_unchanged_pane_prints_nothing_and_stop_is_still_seen(self, monkeypatch, capsys):
        """Skipping the capture never delays Stop or termination checks."""
        session = MagicMock()
        session.name = "child"
        session.tmux_window = "child-w"
        session.status = "running"
        session.parent_session_id = None
        session.oversight_policy = "wait"
        session.oversight_timeout_seconds = 0.0
        sm = MagicMock()
        sm.resolve_session_name.return_value = session
        sm.get_session.return_value = session
        stops = iter([False, False, True])
        monkeypatch.setattr(follow_mode, "SessionManager", lambda: sm)
        monkeypatch.setattr(follow_mode, "_probe_pane", lambda s, w: (1, ("same",)))
        captures = []
        monkeypatch.setattr(follow_mode, "_capture_pane",
                            lambda s, w: captures.append(w) or "hello")
        monkeypatch.setattr(follow_mode, "_check_hook_stop", lambda *a: next(stops))
        monkeypatch.setattr(follow_mode, "_check_report", lambda *a: {"status": "success"})
        monkeypatch.setattr(follow_mode, "_check_session_terminated", lambda *a: False)
        monkeypatch.setattr(follow_mode.time, "sleep", lambda s: None)

        assert follow_mode.follow_agent("child", "agents") == 0
        # One gated capture, two skipped polls, then the final post-Stop capture
        assert len(captures) == 2
        assert capsys.readouterr().out.count("hello") == 1


@pytest.fixture
def server(monkeypatch):
    """An isolated tmux server (own socket) with one ``cat`` window."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    import libtmux

    socket = f"oc-follow-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
    srv = libtmux.Server(socket_name=socket)
    srv.cmd("new-session", "-d", "-s", "agents", "-n", "w", "cat")
    try:
        yield srv
    finally:
        srv.kill()


def _settle(g):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if g.capture("agents", "w") is UNCHANGED:
            return
        time.sleep(0.2)
    raise AssertionError("an idle pane never settled")


class TestRealTmux:

    def test_probe_resolves_only_the_named_window(self, server):
        assert _probe_pane("agents", "w") is not None
        assert _probe_pane("agents", "nope") is None

    def test_an_idle_pane_settles_and_an_in_place_rewrite_is_seen(self, server):
        g = ChangeGatedCapture(keepalive_seconds=60)
        _settle(g)
        server.cmd("send-keys", "-t", "agents:=w", "-l", "spin 1")
        time.sleep(0.3)
        raw = g.capture("agents", "w")
        assert raw is not UNCHANGED and "spin 1" in raw
        _settle(g)
        # Same line, same cursor column, same history: only the text moved
        server.cmd("send-keys", "-t", "agents:=w", "BSpace")
        server.cmd("send-keys", "-t", "agents:=w", "-l", "2")
        time.sleep(0.3)
        raw = g.capture("agents", "w")
        assert raw is not UNCHANGED and "spin 2" in raw
