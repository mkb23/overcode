"""Follow mode's pane polling (#528, audit R16): an idle follower is nearly free.

Each follower (``overcode follow``, ``launch --follow``, ``jobs tail``)
polls its child's pane twice a second. It used to capture 500 lines every
poll; now a one-line ``display-message`` probe at the same rate gates the
capture, so an idle child costs no capture at all and a changed pane is
still captured on the next poll.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import os
import shutil
import time
import uuid

import pytest

pytestmark = [pytest.mark.scale]

FOLLOWERS = 20
POLLS = 20


@pytest.fixture
def server(monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    import libtmux

    socket = f"oc-follow-scale-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
    srv = libtmux.Server(socket_name=socket)
    script = "seq 1 2000 | sed 's/$/ a line of agent output/'; sleep 600"
    srv.cmd("new-session", "-d", "-s", "agents", "-n", "w0", "sh", "-c", script)
    for i in range(1, FOLLOWERS):
        srv.cmd("new-window", "-d", "-t", "agents", "-n", f"w{i}", "sh", "-c", script)
    time.sleep(1.5)  # output done; the activity second is now in the past
    try:
        yield srv
    finally:
        srv.kill()


def test_idle_followers_capture_nothing_after_the_first_poll(server):
    from overcode.follow_mode import UNCHANGED, ChangeGatedCapture

    gates = [ChangeGatedCapture() for _ in range(FOLLOWERS)]
    for poll in range(POLLS):
        for i, gate in enumerate(gates):
            raw = gate.capture("agents", f"w{i}")
            if poll == 0:
                assert raw is not UNCHANGED and "a line of agent output" in raw
    captures = sum(g.captures for g in gates)
    # today: 1 capture per follower (the first poll); was FOLLOWERS * POLLS = 400
    assert captures <= 2 * FOLLOWERS, f"{captures} captures for {FOLLOWERS * POLLS} polls"


def test_a_change_is_still_captured_on_the_next_poll(server):
    from overcode.follow_mode import UNCHANGED, ChangeGatedCapture

    gate = ChangeGatedCapture()
    gate.capture("agents", "w0")
    gate.capture("agents", "w0")
    server.cmd("respawn-pane", "-k", "-t", "agents:=w0", "sh", "-c", "echo fresh-line; sleep 600")
    time.sleep(0.3)
    raw = gate.capture("agents", "w0")
    assert raw is not UNCHANGED and "fresh-line" in raw


@pytest.fixture
def spawns(monkeypatch):
    """Every tmux invocation follow mode makes, as argv lists."""
    from overcode import follow_mode

    calls = []
    real_run = follow_mode.subprocess.run

    def run(argv, *a, **kw):
        calls.append(argv)
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(follow_mode.subprocess, "run", run)
    return calls


def test_idle_followers_spawn_once_per_poll(server, spawns):
    from overcode.follow_mode import ChangeGatedCapture

    gates = [ChangeGatedCapture() for _ in range(FOLLOWERS)]
    for _ in range(POLLS):
        for i, gate in enumerate(gates):
            gate.capture("agents", f"w{i}")
    assert len(spawns) == FOLLOWERS * POLLS


RUNNING_POLLS = 10


def test_a_running_child_costs_one_tmux_command_per_poll(server, spawns):
    """A spinner moves the pane every second, so every poll captures. The
    probe and the capture go to tmux as one chained command: one spawn per
    poll, as before the gate (it was two: probe, then capture)."""
    from overcode.follow_mode import UNCHANGED, ChangeGatedCapture

    server.cmd("new-window", "-d", "-t", "agents", "-n", "busy",
               "sh", "-c", "while :; do printf .; sleep 0.3; done")
    time.sleep(0.5)
    gate = ChangeGatedCapture()
    seen = 0
    for _ in range(RUNNING_POLLS):
        raw = gate.capture("agents", "busy")
        if raw is not UNCHANGED:
            assert raw is not None and "." in raw
            seen += 1
        time.sleep(0.5)  # follow's poll interval
    assert seen == RUNNING_POLLS, f"only {seen} of {RUNNING_POLLS} polls captured a running pane"
    assert len(spawns) == RUNNING_POLLS, f"{len(spawns)} tmux commands for {RUNNING_POLLS} polls"
