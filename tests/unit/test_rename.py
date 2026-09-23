"""`overcode rename` (#478): everything a rename touches besides the CLI.

- SessionManager: the old name becomes an alias (``previous_names``) that
  ``resolve_session_name`` follows; an alias belongs to one agent at a time.
- AgentLauncher.rename on MockTmux: the busy guard, what moves, what is left
  behind, what is sent to the window, and every failure path's rollback.
- AgentLauncher.rename and ``rename_tmux_window`` on a *real* tmux server
  (isolated socket): libtmux's ``Server.cmd`` does not raise on a tmux error
  and a bare ``session:name`` target prefix-matches, which MockTmux cannot
  show — both were bugs in the first version of this feature.
- The status-history readers report a renamed agent's older rows under its
  current name, so its timeline stays one agent.
- The rename note reaches the agent once, through the UserPromptSubmit hook.
- ``follow`` tracks a child renamed mid-follow instead of reporting that it
  terminated (which a parent reads as the child failing).
"""

import json
import os
import shutil
import uuid
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.exceptions import AgentBusyError
from overcode.launcher import AgentLauncher
from overcode.mocks import MockTmux
from overcode.session_manager import SessionManager, rename_notice
from overcode.tmux_manager import TmuxManager


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    # These tests may themselves run inside an overcode agent, whose env
    # would make every launch() a child of it (and point at its tmux socket).
    for var in list(os.environ):
        if var.startswith("OVERCODE_"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
    yield tmp_path


def _sm(tmp_path) -> SessionManager:
    return SessionManager(state_dir=tmp_path, skip_git_detection=True)


def _new(sm: SessionManager, name: str, tmux_session: str = "agents"):
    session = sm.create_session(
        name=name, tmux_session=tmux_session, tmux_window="pending", command=["claude"],
    )
    window = f"{name}-{session.id[:4]}"
    sm.update_session(session.id, tmux_window=window)
    return sm.get_session(session.id)


# ── Aliases ──────────────────────────────────────────────────────────


class TestAliases:

    def test_old_name_resolves_to_the_renamed_agent(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        assert sm.rename_session(a.id, "beta") is True

        assert sm.get_session_by_name("alpha") is None  # exact lookups stay exact
        resolved = sm.resolve_session_name("alpha")
        assert resolved is not None and resolved.id == a.id
        assert resolved.name == "beta"
        assert sm.get_session(a.id).previous_names == ["alpha"]

    def test_rename_notice_only_when_an_alias_was_followed(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        sm.rename_session(a.id, "beta")
        current = sm.get_session(a.id)
        assert rename_notice("beta", current) is None
        assert rename_notice("alpha", current) == "note: agent 'alpha' was renamed to 'beta'"
        assert rename_notice("alpha", None) is None

    def test_repeated_renames_keep_every_old_name_in_order(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "one")
        sm.rename_session(a.id, "two")
        sm.rename_session(a.id, "three")
        assert sm.get_session(a.id).previous_names == ["one", "two"]
        assert sm.resolve_session_name("one").id == a.id
        assert sm.resolve_session_name("two").id == a.id

    def test_renaming_back_to_an_old_name_drops_it_from_the_aliases(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "one")
        sm.rename_session(a.id, "two")
        sm.rename_session(a.id, "one")
        assert sm.get_session(a.id).name == "one"
        assert sm.get_session(a.id).previous_names == ["two"]

    def test_a_live_name_beats_an_alias(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        sm.rename_session(a.id, "beta")
        b = _new(sm, "alpha")  # a new agent takes the old name
        assert sm.resolve_session_name("alpha").id == b.id

    def test_launching_an_agent_under_an_alias_takes_it_over(self, tmp_path):
        """Otherwise the alias would come back to life when the new agent goes."""
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        sm.rename_session(a.id, "beta")
        _new(sm, "alpha")
        assert sm.get_session(a.id).previous_names == []

    def test_an_old_name_belongs_to_its_most_recent_holder(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        sm.rename_session(a.id, "beta")      # a: alpha -> beta
        b = _new(sm, "alpha")                # b takes "alpha" (a loses the alias)
        sm.rename_session(b.id, "gamma")     # b: alpha -> gamma
        assert sm.resolve_session_name("alpha").id == b.id
        assert "alpha" not in sm.get_session(a.id).previous_names

    def test_rename_refuses_a_name_another_agent_has(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        _new(sm, "beta")
        assert sm.rename_session(a.id, "beta") is False
        assert sm.get_session(a.id).name == "alpha"
        assert sm.get_session(a.id).previous_names == []

    def test_previous_names_survive_a_reload(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        sm.rename_session(a.id, "beta")
        assert _sm(tmp_path).get_session(a.id).previous_names == ["alpha"]

    def test_records_written_before_the_field_load_with_no_aliases(self, tmp_path):
        sm = _sm(tmp_path)
        a = _new(sm, "alpha")
        path = tmp_path / "sessions.json"
        data = json.loads(path.read_text())
        del data[a.id]["previous_names"]
        path.write_text(json.dumps(data))
        reloaded = _sm(tmp_path)
        assert reloaded.get_session(a.id).previous_names == []
        assert reloaded.resolve_session_name("nobody") is None


# ── AgentLauncher.rename (MockTmux) ─────────────────────────────────


class TestLauncherRename:

    @staticmethod
    def _launcher(tmp_path, status="waiting_user"):
        tm = TmuxManager("agents", tmux=MockTmux())
        sm = _sm(tmp_path)
        launcher = AgentLauncher("agents", tm, sm)
        launcher._live_status = lambda session: status
        return launcher, sm

    @staticmethod
    def _sent(launcher):
        return launcher.tmux._tmux.sent_keys

    @staticmethod
    def _files(tmp_path, name):
        d = tmp_path / "agents"
        return (
            d / f"hook_state_{name}.json",
            d / f"hook_events_{name}.jsonl",
            d / f"report_{name}.json",
        )

    def _seed_files(self, tmp_path, name):
        for path in self._files(tmp_path, name):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"event": "Stop", "status": "success"}\n')

    def test_relaunch_runs_under_the_new_name_in_the_renamed_window(self, tmp_path):
        launcher, sm = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        launched = len(self._sent(launcher))

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        new_window = f"beta-{sess.id[:4]}"
        relaunch = [k for k in self._sent(launcher)[launched:] if "OVERCODE_SESSION_NAME" in k[2]]
        assert relaunch, "no relaunch command was sent"
        _, window, keys, _ = relaunch[-1]
        assert window == new_window
        assert "OVERCODE_SESSION_NAME=beta" in keys
        assert "--resume" in keys  # same conversation, not a fresh one
        assert sm.get_session(sess.id).stats.current_task == "Renamed from alpha"

    def test_every_name_keyed_file_moves(self, tmp_path):
        launcher, _ = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        self._seed_files(tmp_path, "alpha")

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        assert all(p.exists() for p in self._files(tmp_path, "beta"))
        assert not any(p.exists() for p in self._files(tmp_path, "alpha"))

    def test_leftover_files_under_the_new_name_are_not_inherited(self, tmp_path):
        """A stale report_<new>.json would mark the renamed agent done."""
        launcher, _ = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        self._seed_files(tmp_path, "beta")  # left by an earlier agent called beta

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        assert not any(p.exists() for p in self._files(tmp_path, "beta"))

    def test_hooks_the_old_process_fires_on_its_way_out_are_cleared(self, tmp_path):
        launcher, _ = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        real_send = launcher._send_launch_for_session

        def late_hook_then_relaunch(session, window, **kw):
            # The old process's last hook lands after the files moved.
            self._files(tmp_path, "alpha")[0].write_text('{"event": "SessionEnd"}')
            return real_send(session, window, **kw)

        launcher._send_launch_for_session = late_hook_then_relaunch
        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True
        assert not self._files(tmp_path, "alpha")[0].exists()

    def test_the_agent_gets_a_note_of_its_new_name(self, tmp_path):
        from overcode.hook_handler import get_rename_notice_path

        launcher, _ = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True
        note = get_rename_notice_path("agents", "beta").read_text()
        assert "renamed from 'alpha' to 'beta'" in note

    @pytest.mark.parametrize("status", [
        "running", "running_heartbeat", "busy_sleeping", "heartbeat_start", "waiting_approval",
    ])
    def test_busy_agent_is_refused_and_untouched(self, tmp_path, status):
        launcher, sm = self._launcher(tmp_path, status=status)
        sess = launcher.launch(name="alpha")
        self._seed_files(tmp_path, "alpha")
        sent_before = list(self._sent(launcher))

        with pytest.raises(AgentBusyError) as exc:
            launcher.rename(sess, "beta", graceful_exit_wait=0)

        assert exc.value.status == status
        assert self._sent(launcher) == sent_before  # not even the exit gesture
        assert sm.get_session(sess.id).name == "alpha"
        assert sess.tmux_window in [w["name"] for w in launcher.tmux.list_windows()]
        assert all(p.exists() for p in self._files(tmp_path, "alpha"))

    def test_undetectable_status_is_treated_as_busy(self, tmp_path):
        launcher, sm = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        launcher._live_status = MagicMock(side_effect=RuntimeError("tmux hiccup"))
        with pytest.raises(AgentBusyError) as exc:
            launcher.rename(sess, "beta", graceful_exit_wait=0)
        assert exc.value.status == "unknown"
        assert sm.get_session(sess.id).name == "alpha"

    @pytest.mark.parametrize("status", ["waiting_user", "error", "waiting_oversight", "terminated"])
    def test_idle_statuses_rename(self, tmp_path, status):
        launcher, sm = self._launcher(tmp_path, status=status)
        sess = launcher.launch(name="alpha")
        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True
        assert sm.get_session(sess.id).name == "beta"

    def test_force_renames_a_busy_agent(self, tmp_path):
        launcher, sm = self._launcher(tmp_path, status="running")
        sess = launcher.launch(name="alpha")
        assert launcher.rename(sess, "beta", force=True, graceful_exit_wait=0) is True
        assert sm.get_session(sess.id).name == "beta"

    def test_dead_agent_is_not_status_checked_or_relaunched(self, tmp_path):
        launcher, sm = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        launcher.tmux.kill_window(sess.tmux_window)
        launcher._live_status = MagicMock(side_effect=AssertionError("no window to check"))
        sent_before = len(self._sent(launcher))

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        assert len(self._sent(launcher)) == sent_before
        renamed = sm.get_session(sess.id)
        assert renamed.name == "beta"
        assert renamed.tmux_window == f"beta-{sess.id[:4]}"  # where revive will put it

    def test_window_rename_refused_restores_the_agent_under_its_old_name(self, tmp_path):
        launcher, sm = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        self._seed_files(tmp_path, "alpha")
        launcher.tmux.rename_window = lambda old, new: False
        sent_before = len(self._sent(launcher))

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is False

        unchanged = sm.get_session(sess.id)
        assert unchanged.name == "alpha" and unchanged.previous_names == []
        assert unchanged.tmux_window == sess.tmux_window
        assert all(p.exists() for p in self._files(tmp_path, "alpha"))
        relaunch = [k for k in self._sent(launcher)[sent_before:] if "OVERCODE_SESSION_NAME" in k[2]]
        assert relaunch and relaunch[-1][1] == sess.tmux_window
        assert "OVERCODE_SESSION_NAME=alpha" in relaunch[-1][2]

    def test_losing_the_race_rolls_everything_back_and_restores_the_agent(self, tmp_path):
        launcher, sm = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        other = launcher.launch(name="other")
        self._seed_files(tmp_path, "alpha")
        real_rename = sm.rename_session

        def someone_else_wins(session_id, new_name, **fields):
            real_rename(other.id, new_name)  # a concurrent rename commits first
            return real_rename(session_id, new_name, **fields)

        sm.rename_session = someone_else_wins
        sent_before = len(self._sent(launcher))

        with pytest.raises(ValueError, match="already exists"):
            launcher.rename(sess, "beta", graceful_exit_wait=0)

        assert sm.get_session(sess.id).name == "alpha"
        assert sess.tmux_window in [w["name"] for w in launcher.tmux.list_windows()]
        assert all(p.exists() for p in self._files(tmp_path, "alpha"))
        relaunch = [k for k in self._sent(launcher)[sent_before:] if "OVERCODE_SESSION_NAME" in k[2]]
        assert relaunch and "OVERCODE_SESSION_NAME=alpha" in relaunch[-1][2]

    def test_agent_in_another_tmux_session_is_renamed_there(self, tmp_path):
        """The launcher's own session is only the CLI's --session default."""
        mock = MockTmux()
        sm = _sm(tmp_path)
        other = AgentLauncher("work", TmuxManager("work", tmux=mock), sm)
        other._live_status = lambda session: "waiting_user"
        sess = other.launch(name="alpha")
        (tmp_path / "work").mkdir(exist_ok=True)
        (tmp_path / "work" / "hook_state_alpha.json").write_text('{"event": "Stop"}')

        launcher = AgentLauncher("agents", TmuxManager("agents", tmux=mock), sm)
        with patch.object(AgentLauncher, "_live_status", lambda self, s: "waiting_user"):
            assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        assert f"beta-{sess.id[:4]}" in mock.sessions["work"]
        assert (tmp_path / "work" / "hook_state_beta.json").exists()
        assert not (tmp_path / "agents" / "hook_state_beta.json").exists()

    @pytest.mark.parametrize("bad, match", [
        ("daemon_claude", "reserved"),
        ("alpha", "already called"),
    ])
    def test_reserved_or_unchanged_names_are_refused(self, tmp_path, bad, match):
        launcher, _ = self._launcher(tmp_path)
        sess = launcher.launch(name="alpha")
        with pytest.raises(ValueError, match=match):
            launcher.rename(sess, bad, graceful_exit_wait=0)


# ── Real tmux ────────────────────────────────────────────────────────


@pytest.fixture
def real_tmux(monkeypatch):
    """An isolated tmux server (own socket) with an ``agents`` session."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    import libtmux

    socket = f"oc-rename-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
    server = libtmux.Server(socket_name=socket)
    server.cmd("new-session", "-d", "-s", "agents", "-n", "placeholder", "sh")
    try:
        yield server
    finally:
        server.kill()


def _window_names(server, session="agents"):
    return server.cmd("list-windows", "-t", session, "-F", "#{window_name}").stdout


class TestRealTmux:

    def test_rename_reports_failure_when_the_window_is_gone(self, real_tmux):
        from overcode.tmux_utils import rename_tmux_window

        assert rename_tmux_window(real_tmux, "agents", "no-such-window", "x") is False

    def test_rename_never_prefix_matches_another_window(self, real_tmux):
        """A bare `agents:foo-1234` target would rename `foo-1234x`."""
        from overcode.tmux_utils import rename_tmux_window

        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", "foo-1234x", "sh")
        assert rename_tmux_window(real_tmux, "agents", "foo-1234", "HIJACKED") is False
        assert "foo-1234x" in _window_names(real_tmux)
        assert "HIJACKED" not in _window_names(real_tmux)

    def test_rename_renames_the_named_window(self, real_tmux):
        from overcode.tmux_utils import rename_tmux_window

        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", "foo-1234", "sh")
        assert rename_tmux_window(real_tmux, "agents", "foo-1234", "bar-1234") is True
        assert "bar-1234" in _window_names(real_tmux)
        assert "foo-1234" not in _window_names(real_tmux)

    def test_real_tmux_and_tmux_manager_report_failure(self, real_tmux):
        from overcode.implementations import RealTmux

        assert RealTmux(socket_name=os.environ["OVERCODE_TMUX_SOCKET"]).rename_window(
            "agents", "missing", "x",
        ) is False
        assert TmuxManager("agents").rename_window("missing", "x") is False

    def test_end_to_end_on_a_real_server(self, real_tmux, tmp_path):
        """Window renamed exactly, a look-alike window untouched, files rekeyed,
        relaunch aimed at the renamed window — no real agent CLI is run."""
        sm = _sm(tmp_path)
        sess = _new(sm, "alpha")
        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", sess.tmux_window, "sh")
        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", sess.tmux_window + "x", "sh")
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "agents" / "hook_state_alpha.json").write_text('{"event": "Stop"}')

        launcher = AgentLauncher("agents", session_manager=sm)
        launcher._live_status = lambda session: "waiting_user"
        relaunched = []
        launcher._send_launch_for_session = (
            lambda session, window, **kw: relaunched.append((session.name, window)) or True
        )

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is True

        names = _window_names(real_tmux)
        assert f"beta-{sess.id[:4]}" in names
        assert sess.tmux_window not in names
        assert sess.tmux_window + "x" in names  # the look-alike is untouched
        assert (tmp_path / "agents" / "hook_state_beta.json").exists()
        assert relaunched == [("beta", f"beta-{sess.id[:4]}")]

    def test_end_to_end_window_gone_mid_rename_restores_old_identity(self, real_tmux, tmp_path):
        """The window vanishes between the existence check and the rename:
        real tmux refuses, and nothing is renamed."""
        sm = _sm(tmp_path)
        sess = _new(sm, "alpha")
        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", sess.tmux_window, "sh")
        real_tmux.cmd("new-window", "-d", "-t", "agents", "-n", sess.tmux_window + "x", "sh")

        launcher = AgentLauncher("agents", session_manager=sm)
        launcher._live_status = lambda session: "waiting_user"
        launcher._send_graceful_exit = lambda backend, window: real_tmux.cmd(
            "kill-window", "-t", f"agents:={window}",
        )
        relaunched = []
        launcher._send_launch_for_session = (
            lambda session, window, **kw: relaunched.append((session.name, window)) or True
        )

        assert launcher.rename(sess, "beta", graceful_exit_wait=0) is False

        assert sm.get_session(sess.id).name == "alpha"
        assert sess.tmux_window + "x" in _window_names(real_tmux)
        assert relaunched == [("alpha", sess.tmux_window)]


# ── Status history follows the rename ────────────────────────────────


def _write_history(path, rows):
    with open(path, "w") as f:
        f.write("timestamp,agent,status,activity,session_id,hostname\n")
        for ts, agent, status, sid in rows:
            f.write(f"{ts.isoformat()},{agent},{status},,{sid},host\n")


class TestStatusHistory:

    def test_old_rows_are_reported_under_the_current_name(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now()
        path = tmp_path / "h.csv"
        _write_history(path, [
            (now - timedelta(minutes=30), "alpha", "running", "sid-1"),
            (now - timedelta(minutes=20), "alpha", "waiting_user", "sid-1"),
            (now - timedelta(minutes=10), "beta", "running", "sid-1"),
            (now - timedelta(minutes=5), "other", "running", "sid-2"),
            (now - timedelta(minutes=4), "legacy", "running", ""),  # pre-0.3.6, no id
        ])
        rows = StatusHistoryFile(path).read(hours=1)
        assert [r[1] for r in rows] == ["beta", "beta", "beta", "other", "legacy"]

    def test_filtering_by_the_new_name_includes_rows_from_before(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now()
        path = tmp_path / "h.csv"
        _write_history(path, [
            (now - timedelta(minutes=30), "alpha", "running", "sid-1"),
            (now - timedelta(minutes=10), "beta", "waiting_user", "sid-1"),
        ])
        rows = StatusHistoryFile(path).read(hours=1, agent_name="beta")
        assert [r[2] for r in rows] == ["running", "waiting_user"]

    def test_a_rename_appended_later_is_picked_up_incrementally(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now()
        path = tmp_path / "h.csv"
        _write_history(path, [(now - timedelta(minutes=30), "alpha", "running", "sid-1")])
        reader = StatusHistoryFile(path)
        assert [r[1] for r in reader.read(hours=1)] == ["alpha"]
        with open(path, "a") as f:
            f.write(f"{(now - timedelta(minutes=1)).isoformat()},beta,running,,sid-1,host\n")
        assert [r[1] for r in reader.read(hours=1)] == ["beta", "beta"]

    def test_the_carry_row_takes_the_current_name(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now()
        path = tmp_path / "h.csv"
        _write_history(path, [
            (now - timedelta(minutes=62), "alpha", "running", "sid-1"),  # carry (before cutoff)
            (now - timedelta(minutes=10), "beta", "waiting_user", "sid-1"),
        ])
        rows = StatusHistoryFile(path).read(hours=1, carry=True)
        assert [(r[1], r[2]) for r in rows] == [("beta", "running"), ("beta", "waiting_user")]

    def test_a_reused_name_keeps_its_own_rows(self, tmp_path):
        """A new agent that takes a renamed agent's old name is a different id."""
        from overcode.status_history import StatusHistoryFile

        now = datetime.now()
        path = tmp_path / "h.csv"
        _write_history(path, [
            (now - timedelta(minutes=30), "alpha", "running", "sid-1"),
            (now - timedelta(minutes=20), "beta", "running", "sid-1"),
            (now - timedelta(minutes=10), "alpha", "waiting_user", "sid-2"),
        ])
        rows = StatusHistoryFile(path).read(hours=1)
        assert [(r[1], r[4]) for r in rows] == [
            ("beta", "sid-1"), ("beta", "sid-1"), ("alpha", "sid-2"),
        ]

    def test_the_range_reader_relabels_across_archives_too(self, tmp_path):
        import gzip

        from overcode.status_history import read_agent_status_history_range

        now = datetime.now()
        path = tmp_path / "agent_status_history.csv"
        _write_history(path, [(now - timedelta(minutes=10), "beta", "running", "sid-1")])
        archive = tmp_path / f"agent_status_history.{now.strftime('%Y%m%d-%H%M%S')}.csv.gz"
        with gzip.open(archive, "wt") as f:
            f.write("timestamp,agent,status,activity,session_id,hostname\n")
            f.write(f"{(now - timedelta(hours=5)).isoformat()},alpha,running,,sid-1,host\n")
        rows = read_agent_status_history_range(now - timedelta(hours=6), now, path)
        assert [r[1] for r in rows] == ["beta", "beta"]


# ── The rename note reaches the agent once ──────────────────────────


class TestRenameNoticeHook:

    def test_emitted_once_then_gone(self, tmp_path, capsys):
        from overcode.hook_handler import _emit_rename_notice, get_rename_notice_path

        path = get_rename_notice_path("agents", "beta")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[overcode] This agent was renamed from 'alpha' to 'beta'.\n")

        _emit_rename_notice("agents", "beta")
        assert "renamed from 'alpha' to 'beta'" in capsys.readouterr().out
        assert not path.exists()

        _emit_rename_notice("agents", "beta")
        assert capsys.readouterr().out == ""

    def test_user_prompt_submit_prints_the_note(self, tmp_path, monkeypatch, capsys):
        import io

        from overcode import hook_handler

        path = hook_handler.get_rename_notice_path("agents", "beta")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[overcode] renamed from 'alpha' to 'beta'\n")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "beta")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
            "hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "hi",
        })))

        hook_handler.handle_hook_event()

        assert "renamed from 'alpha' to 'beta'" in capsys.readouterr().out
        assert not path.exists()


# ── follow keeps following a renamed child ──────────────────────────


class TestFollowAcrossRename:

    @staticmethod
    def _sessions(names):
        """A SessionManager stub whose agent is renamed as polls go by."""
        seq = iter(names)
        state = {"name": next(seq)}
        sm = MagicMock()

        def get_session(session_id):
            session = SimpleNamespace(
                id="sid-1", name=state["name"], tmux_window=f"{state['name']}-sid1",
                status="running",
            )
            state["name"] = next(seq, state["name"])
            return session

        sm.get_session.side_effect = get_session
        return sm

    def test_check_terminated_by_id_is_not_fooled_by_a_rename(self):
        from overcode.follow_mode import _check_session_terminated

        sm = MagicMock()
        sm.get_session_by_name.return_value = None  # the old name is gone
        sm.get_session.return_value = SimpleNamespace(status="running")
        assert _check_session_terminated(sm, "alpha", "sid-1") is False
        assert _check_session_terminated(sm, "alpha") is True  # the old, name-only view

    def test_poll_for_report_finds_the_report_under_the_new_name(self, capsys):
        from overcode import follow_mode

        sm = self._sessions(["alpha", "beta"])
        looked_for = []

        def check_report(tmux_session, name):
            looked_for.append(name)
            return {"status": "success"} if name == "beta" else None

        with patch.object(follow_mode, "_check_report", side_effect=check_report), \
                patch.object(follow_mode, "_capture_pane", return_value=""), \
                patch.object(follow_mode.time, "sleep"):
            result = follow_mode._poll_for_report(
                "alpha", "agents", sm, "alpha-sid1", "wait", 0.0, 0.1, deque(),
                session_id="sid-1",
            )

        assert result == 0
        assert looked_for[-1] == "beta"
        err = capsys.readouterr().err
        assert "renamed to 'beta'" in err
        assert "terminated" not in err

    def test_follow_agent_by_old_name_follows_the_renamed_agent(self, capsys):
        from overcode import follow_mode

        current = SimpleNamespace(
            id="sid-1", name="beta", tmux_window="beta-sid1", status="running",
            oversight_policy="wait", oversight_timeout_seconds=0.0,
        )
        sm = MagicMock()
        sm.resolve_session_name.return_value = current
        sm.get_session.return_value = current
        captured = []

        with patch.object(follow_mode, "SessionManager", return_value=sm), \
                patch.object(follow_mode, "_capture_pane",
                             side_effect=lambda s, w: captured.append(w) or "out"), \
                patch.object(follow_mode, "_check_hook_stop", return_value=True), \
                patch.object(follow_mode, "_check_report", return_value={"status": "success"}), \
                patch.object(follow_mode.time, "sleep"):
            assert follow_mode.follow_agent("alpha", "agents") == 0

        assert set(captured) == {"beta-sid1"}
        assert "'alpha' was renamed to 'beta'" in capsys.readouterr().err
