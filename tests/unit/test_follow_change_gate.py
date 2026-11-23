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
from overcode.follow_mode import (
    UNCHANGED, ChangeGatedCapture, _capture_pane, _probe_and_capture, _probe_pane,
)


@pytest.fixture
def gate(monkeypatch):
    """A gate over scripted probes, counting tmux spawns, with a settable clock.

    ``spawns`` logs each tmux invocation: "both" (probe and capture chained
    in one), "probe" or "capture". Every capture's text is numbered.
    """
    state = {"probes": iter(()), "wall": 1000.5, "mono": 0.0, "spawns": [], "texts": 0}

    def text():
        state["texts"] += 1
        return f"text {state['texts']}"

    def probe(session, window):
        state["spawns"].append("probe")
        return next(state["probes"])

    def capture(session, window):
        state["spawns"].append("capture")
        return text()

    def both(session, window, lines=None):
        state["spawns"].append("both")
        return next(state["probes"]), text()

    monkeypatch.setattr(follow_mode, "_probe_pane", probe)
    monkeypatch.setattr(follow_mode, "_capture_pane", capture)
    monkeypatch.setattr(follow_mode, "_probe_and_capture", both)
    monkeypatch.setattr(follow_mode, "_wall_clock", lambda: state["wall"])
    monkeypatch.setattr(follow_mode, "_monotonic", lambda: state["mono"])

    def make(probes, keepalive=5.0):
        state["probes"] = iter(probes)
        return ChangeGatedCapture(keepalive_seconds=keepalive)

    make.state = state
    return make


class TestChangeGatedCapture:

    def test_first_poll_captures_in_one_spawn(self, gate):
        g = gate([(999, ("a",))])
        assert g.capture("agents", "w") == "text 1"
        assert gate.state["spawns"] == ["both"]

    def test_a_pane_quiet_since_before_the_capture_is_not_captured_again(self, gate):
        g = gate([(999, ("a",))] * 3)
        g.capture("agents", "w")
        gate.state["wall"] += 0.5
        gate.state["mono"] += 0.5
        assert g.capture("agents", "w") is UNCHANGED
        assert g.capture("agents", "w") is UNCHANGED
        assert g.captures == 1 and g.skipped == 2
        # The poll after a capture chains the capture and drops it; once
        # unchanged, only the probe runs.
        assert gate.state["spawns"] == ["both", "both", "probe"]

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

    def test_a_running_pane_costs_one_spawn_per_poll(self, gate):
        """A spinner moves the activity every second: every poll captures,
        and each capture is one tmux invocation, not a probe then a capture."""
        g = gate([(1000 + i, ("a", str(i))) for i in range(10)])
        for i in range(10):
            gate.state["wall"] = 1000.5 + i
            assert g.capture("agents", "w") == f"text {i + 1}"
        assert gate.state["spawns"] == ["both"] * 10
        assert g.spawns == 10

    def test_a_change_after_a_quiet_poll_is_captured_on_that_poll(self, gate):
        g = gate([(999, ("a",)), (999, ("a",)), (999, ("a",)), (1001, ("b",)), (1002, ("c",))])
        g.capture("agents", "w")
        g.capture("agents", "w")
        assert g.capture("agents", "w") is UNCHANGED
        gate.state["wall"] = 1001.5
        assert g.capture("agents", "w") == "text 3"   # texts 1-2 were chained
        gate.state["wall"] = 1002.5
        assert g.capture("agents", "w") == "text 4"
        assert gate.state["spawns"] == ["both", "both", "probe", "probe", "capture", "both"]

    def test_without_a_probe_answer_every_poll_captures(self, gate):
        g = gate([None, None])
        assert g.capture("agents", "w") == "text 1"
        assert g.capture("agents", "w") == "text 2"
        assert gate.state["spawns"] == ["both", "both"]

    def test_a_probe_lost_after_a_quiet_poll_still_captures(self, gate):
        """The window went away: the caller still sees the capture's answer."""
        g = gate([(999, ("a",)), (999, ("a",)), None, None])
        g.capture("agents", "w")
        g.capture("agents", "w")
        assert g.capture("agents", "w") == "text 3"
        assert g.capture("agents", "w") == "text 4"
        assert gate.state["spawns"] == ["both", "both", "probe", "capture", "both"]

    def test_a_failed_chained_call_is_none(self, gate, monkeypatch):
        monkeypatch.setattr(follow_mode, "_probe_and_capture", lambda s, w, lines=None: (None, None))
        g = gate([])
        assert g.capture("agents", "w") is None
        assert g.capture("agents", "w") is None

    def test_keepalive_captures_an_unchanged_pane(self, gate):
        g = gate([(999, ("a",))] * 2)
        g.capture("agents", "w")
        gate.state["mono"] += 5.0
        assert g.capture("agents", "w") == "text 2"


class TestProbeAndCapture:

    PROBE = "agents\tw\t3\t1000\t5\t0\t1\t80\t24"

    def _run(self, returncode, stdout):
        result = MagicMock(returncode=returncode, stdout=stdout)
        with patch("overcode.follow_mode.subprocess.run", return_value=result) as run:
            out = _probe_and_capture("agents", "w", lines=50)
        return out, run.call_args.args[0]

    def test_one_tmux_command_chains_the_probe_and_the_capture(self):
        (probe, raw), argv = self._run(0, self.PROBE + "\nline 1\n\nline 2\n")
        assert probe == (1000, tuple(self.PROBE.split("\t")))
        assert raw == "line 1\n\nline 2\n"   # the capture's bytes, untouched
        cut = argv.index(";")
        assert argv[cut - 5:cut - 1] == ["display-message", "-p", "-t", "agents:=w"]
        assert argv[cut + 1:] == ["capture-pane", "-t", "agents:=w", "-p", "-S", "-50"]

    def test_a_failed_chain_is_no_probe_and_no_text(self):
        """A missing window: display-message answers for another window, then
        capture-pane fails and tmux exits non-zero."""
        assert self._run(1, "agents\tother\t2\t1\t0\t0\t0\t80\t24\n")[0] == (None, None)

    def test_another_window_is_no_probe_but_keeps_the_text(self):
        (probe, raw), _ = self._run(0, self.PROBE.replace("\tw\t", "\tother\t") + "\nx\n")
        assert probe is None and raw == "x\n"

    def test_no_answer_is_none(self):
        assert self._run(0, "")[0] == (None, None)
        import subprocess
        with patch("overcode.follow_mode.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5)):
            assert _probe_and_capture("agents", "w") == (None, None)


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
        monkeypatch.setattr(follow_mode, "_probe_and_capture",
                            lambda s, w, lines=None: ((1, ("same",)), "hello"))
        monkeypatch.setattr(follow_mode, "_check_hook_stop", lambda *a: next(stops))
        monkeypatch.setattr(follow_mode, "_check_report", lambda *a: {"status": "success"})
        monkeypatch.setattr(follow_mode, "_check_session_terminated", lambda *a: False)
        monkeypatch.setattr(follow_mode.time, "sleep", lambda s: None)

        assert follow_mode.follow_agent("child", "agents") == 0
        # Polls: a chained capture, then two skipped (the first drops its
        # chained text); then the final post-Stop capture
        assert len(captures) == 1
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

    def test_the_chained_capture_is_byte_identical_to_capture_pane(self, server):
        server.cmd("send-keys", "-t", "agents:=w", "-l", "hello  ")
        time.sleep(0.3)
        probe, raw = _probe_and_capture("agents", "w")
        assert probe == _probe_pane("agents", "w")
        assert raw == _capture_pane("agents", "w") and "hello" in raw

    def test_a_missing_window_chains_to_nothing(self, server):
        assert _probe_and_capture("agents", "nope") == (None, None)
        assert _capture_pane("agents", "nope") is None
        assert _probe_and_capture("gone", "w") == (None, None)

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
