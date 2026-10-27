"""Cost budgets gate prompts for every backend (#530, budget_gate).

- A prompt to an agent over its budget is refused on every path that types
  into it (the launcher's send, so `overcode send`, the TUI, the web API,
  the supervisor, standing instructions, a parent's messages and the
  release notice), for every backend. Nothing is typed; the sender is told
  the budget and how to raise it.
- Keys and gestures always go through: Enter, Escape, approve, reject, a
  menu number pressed in the TUI, text without Enter.
- `overcode send --over-budget` sends anyway, and leaves a one-shot permit
  Claude's own hook honours.
- A refusal is stamped on the agent; the daemon shows it red "rejected"
  (as Claude's hook does) while the budget stays exceeded and the agent is
  not working, and clears the stamp once the budget is raised.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode import budget_gate
from overcode.launcher import AgentLauncher
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

pytestmark = pytest.mark.unit

BACKENDS = ("claude-code", "opencode", "opencode2", "codex", "grok", "hermes")


def _over_budget(sm, name="kid", backend="claude-code", cost=5.42, budget=5.0, **kw):
    session = sm.create_session(name=name, tmux_session="agents", tmux_window=name,
                                command=["x"], backend=backend)
    if kw:
        sm.update_session(session.id, **kw)
    sm.set_cost_budget(session.id, budget)
    sm.update_stats(session.id, estimated_cost_usd=cost)
    return sm.get_session(session.id)


@pytest.fixture
def rig(tmp_path):
    tmux = MockTmux()
    tmux.new_session("agents")
    sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
    launcher = AgentLauncher(tmux_session="agents",
                             tmux_manager=TmuxManager("agents", tmux=tmux),
                             session_manager=sm)
    return launcher, sm, tmux


def _typed(tmux):
    return [keys for (_s, _w, keys, _e) in tmux.sent_keys]


# --- the pure parts -----------------------------------------------------------

class TestPure:
    def test_is_budget_exceeded(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        over = _over_budget(sm, "a")
        under = _over_budget(sm, "b", cost=1.0)
        none = _over_budget(sm, "c", budget=0.0)
        assert budget_gate.is_budget_exceeded(over)
        assert not budget_gate.is_budget_exceeded(under)
        assert not budget_gate.is_budget_exceeded(none)  # 0: no budget
        assert not budget_gate.is_budget_exceeded(MagicMock())

    @pytest.mark.parametrize("text,enter,prompt", [
        ("do more", True, True),
        ("do more", False, False),     # not submitted
        ("", True, False),             # Enter alone
        ("Escape", True, False),
        ("approve", True, False),
        ("reject", True, False),
        ("enter", True, False),
    ])
    def test_is_prompt(self, text, enter, prompt):
        from overcode.agent_io import AgentIOMixin
        assert budget_gate.is_prompt(text, enter, AgentIOMixin.SEND_KEYS) is prompt

    def test_refusal_names_the_budget_and_how_to_raise_it(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        kid = _over_budget(sm)
        reason = budget_gate.refusal(kid, sm)
        assert "$5.42 of $5.00" in reason
        assert "overcode budget set kid <amount>" in reason

    def test_a_child_with_a_budgeted_parent_is_told_to_transfer(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        boss = _over_budget(sm, "boss", cost=0.0, budget=20.0)
        kid = _over_budget(sm, "kid", parent_session_id=boss.id)
        assert "overcode budget transfer boss kid <amount>" in budget_gate.refusal(kid, sm)

    def test_no_refusal_under_budget_or_for_a_remote_agent(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        assert budget_gate.refusal(_over_budget(sm, "a", cost=1.0), sm) is None
        remote = _over_budget(sm, "b")
        remote.is_remote = True
        assert budget_gate.refusal(remote, sm) is None

    def test_permit_is_one_shot_and_expires(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        budget_gate.write_permit("agents", "sid-1", now=1000.0)
        assert budget_gate.take_permit("agents", "sid-1", now=1001.0) is True
        assert budget_gate.take_permit("agents", "sid-1", now=1001.0) is False
        budget_gate.write_permit("agents", "sid-1", now=1000.0)
        late = 1000.0 + budget_gate.PERMIT_SECONDS + 1
        assert budget_gate.take_permit("agents", "sid-1", now=late) is False
        assert budget_gate.take_permit("agents", None) is False

    def test_rejection_applies(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        kid = _over_budget(sm)
        assert not budget_gate.rejection_applies(kid, "waiting_user")  # nothing refused
        kid.budget_rejected_at = "2026-10-27T10:00:00+00:00"
        assert budget_gate.rejection_applies(kid, "waiting_user")
        assert not budget_gate.rejection_applies(kid, "running")      # working
        assert not budget_gate.rejection_applies(kid, "terminated")
        kid.cost_budget_usd = 50.0
        assert not budget_gate.rejection_applies(kid, "waiting_user")  # raised

    def test_rejected_detail_is_claudes(self):
        from overcode.status_constants import STATUS_COLOR_RED, STATUS_ERROR
        detail = budget_gate.rejected_detail()
        assert detail.color == STATUS_COLOR_RED
        assert detail.legacy_status == STATUS_ERROR
        assert [(b.kind, b.label) for b in detail.badges] == [("error", "rejected")]


# --- the launcher: every path that types into an agent ------------------------

class TestLauncherGate:
    @pytest.mark.parametrize("backend", BACKENDS)
    def test_a_prompt_is_refused_for_every_backend(self, rig, backend):
        launcher, sm, tmux = rig
        kid = _over_budget(sm, backend=backend)
        assert launcher.send_to_session("kid", "keep going") is False
        assert "keep going" not in _typed(tmux)
        assert "over its cost budget" in launcher.send_refusal
        assert sm.get_session(kid.id).budget_rejected_at

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_by_id_too(self, rig, backend):
        launcher, sm, tmux = rig
        kid = _over_budget(sm, backend=backend)
        assert launcher.send_to_session_by_id(kid.id, "keep going") is False
        assert "keep going" not in _typed(tmux)

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.parametrize("key", ["escape", "esc", "enter", "approve", "reject"])
    def test_keys_and_gestures_always_go(self, rig, backend, key):
        launcher, sm, tmux = rig
        _over_budget(sm, backend=backend)
        assert launcher.send_to_session("kid", key) is True
        assert launcher.send_refusal is None

    def test_text_without_enter_goes(self, rig):
        launcher, sm, tmux = rig
        _over_budget(sm)
        assert launcher.send_to_session("kid", "y", enter=False) is True
        assert "y" in _typed(tmux)

    def test_a_tui_keypress_goes(self, rig):
        launcher, sm, tmux = rig
        kid = _over_budget(sm)
        assert launcher.send_to_session_by_id(kid.id, "1", enter=True, keypress=True) is True
        assert "1" in _typed(tmux)

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_over_budget_sends_permits_and_clears_the_stamp(self, rig, backend, tmp_path,
                                                            monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        launcher, sm, tmux = rig
        kid = _over_budget(sm, backend=backend)
        launcher.send_to_session("kid", "refused first")
        assert launcher.send_to_session("kid", "wrap up", over_budget=True) is True
        assert "wrap up" in _typed(tmux)
        assert sm.get_session(kid.id).budget_rejected_at is None
        assert budget_gate.take_permit("agents", kid.id) is True

    def test_under_budget_is_untouched(self, rig):
        launcher, sm, tmux = rig
        kid = _over_budget(sm, cost=1.0)
        assert launcher.send_to_session("kid", "hello") is True
        assert sm.get_session(kid.id).budget_rejected_at is None

    def test_release_notice_says_why_it_was_not_told(self, rig):
        from overcode.lifecycle import release_agent
        launcher, sm, tmux = rig
        boss = _over_budget(sm, "boss", cost=0.0, budget=0.0)
        kid = _over_budget(sm, "kid", parent_session_id=boss.id)
        result = release_agent(launcher, sm.get_session(kid.id))
        assert result.ok and not result.notified
        assert result.note == "not told: over its cost budget"


# --- overcode send -----------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def cli_fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    sm = SessionManager(skip_git_detection=True)
    return sm


class TestCliSend:
    def _send(self, *args, sent=True):
        from overcode.cli import app
        with patch("overcode.cli.agent.AgentLauncher") as cls, \
                patch("overcode.cli.split.open_split"):
            launcher = MagicMock()
            launcher.send_to_session.return_value = sent
            launcher.send_refusal = None
            cls.return_value = launcher
            return runner.invoke(app, ["send", *args]), launcher

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_refused_before_anything_is_typed(self, cli_fleet, backend):
        kid = _over_budget(cli_fleet, backend=backend)
        result, launcher = self._send("kid", "keep", "going")
        assert result.exit_code == 1
        assert "Not sent" in result.output and "$5.42 of $5.00" in result.output
        assert "overcode budget set kid" in result.output
        assert "--over-budget" in result.output
        launcher.send_to_session.assert_not_called()
        assert SessionManager(skip_git_detection=True).get_session(kid.id).budget_rejected_at

    def test_not_woken_when_refused(self, cli_fleet):
        kid = _over_budget(cli_fleet)
        cli_fleet.update_session(kid.id, is_asleep=True)
        result, _ = self._send("kid", "hello")
        assert result.exit_code == 1
        assert SessionManager(skip_git_detection=True).get_session(kid.id).is_asleep

    @pytest.mark.parametrize("gesture", ["escape", "approve", "reject", "enter"])
    def test_gestures_go(self, cli_fleet, gesture):
        _over_budget(cli_fleet)
        result, launcher = self._send("kid", gesture)
        assert result.exit_code == 0
        launcher.send_to_session.assert_called_once()

    def test_no_enter_goes(self, cli_fleet):
        _over_budget(cli_fleet)
        result, launcher = self._send("kid", "--no-enter", "y")
        assert result.exit_code == 0

    def test_over_budget_flag_sends(self, cli_fleet):
        _over_budget(cli_fleet)
        result, launcher = self._send("kid", "--over-budget", "wrap", "up")
        assert result.exit_code == 0, result.output
        _, kwargs = launcher.send_to_session.call_args
        assert kwargs["over_budget"] is True

    def test_launcher_refusal_is_reported(self, cli_fleet):
        """A refusal the launcher makes (a race with the pre-check) reads the same."""
        from overcode.cli import app
        _over_budget(cli_fleet, cost=1.0)
        with patch("overcode.cli.agent.AgentLauncher") as cls:
            launcher = MagicMock()
            launcher.send_to_session.return_value = False
            launcher.send_refusal = "'kid' is over its cost budget ($9.00 of $5.00): not sent."
            cls.return_value = launcher
            result = runner.invoke(app, ["send", "kid", "hello"])
        assert result.exit_code == 1
        assert "Not sent" in result.output and "--over-budget" in result.output


# --- web API -----------------------------------------------------------------

class TestWebApi:
    def test_send_is_refused_with_409(self, cli_fleet):
        from overcode.web_control_api import ControlError, send_to_agent
        _over_budget(cli_fleet)
        with patch("overcode.launcher.AgentLauncher") as cls:
            launcher = MagicMock()
            launcher.send_to_session.return_value = False
            launcher.send_refusal = "'kid' is over its cost budget: not sent."
            cls.return_value = launcher
            with pytest.raises(ControlError) as err:
                send_to_agent("agents", "kid", "hello")
        assert err.value.status == 409
        assert "cost budget" in str(err.value)

    def test_over_budget_is_passed_through(self, cli_fleet):
        from overcode.web_control_api import send_to_agent
        _over_budget(cli_fleet)
        with patch("overcode.launcher.AgentLauncher") as cls:
            launcher = MagicMock()
            launcher.send_to_session.return_value = True
            cls.return_value = launcher
            assert send_to_agent("agents", "kid", "hello", over_budget=True) == {"ok": True}
        assert launcher.send_to_session.call_args.kwargs["over_budget"] is True

    def test_route_reads_over_budget(self):
        from overcode.web_server import _AGENT_CONTROL_ROUTES
        api = MagicMock()
        _AGENT_CONTROL_ROUTES[("POST", "send")](api, "agents", "kid",
                                                {"text": "x", "over_budget": True})
        assert api.send_to_agent.call_args.kwargs["over_budget"] is True


# --- heartbeat and supervisor: already skip an over-budget agent --------------

class TestAutomatedNudges:
    def test_heartbeat_not_eligible(self):
        from overcode.monitor_daemon_core import is_heartbeat_eligible
        assert not is_heartbeat_eligible(True, False, False, False, True, True)

    def test_supervisor_skips(self):
        from overcode.supervisor_daemon_core import filter_non_green_sessions
        sessions = [{"name": "kid", "current_status": "waiting_user", "budget_exceeded": True}]
        assert filter_non_green_sessions(sessions) == []


# --- Claude's hook honours the permit -----------------------------------------

class TestHookPermit:
    def _state(self, tmp_path):
        state_dir = tmp_path / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "monitor_daemon_state.json").write_text(json.dumps({
            "sessions": [{"name": "test-agent", "session_id": "sid-1",
                          "budget_exceeded": True, "cost_budget_usd": 5.0,
                          "estimated_cost_usd": 5.42}]}))

    def _prompt(self):
        from overcode.hook_handler import handle_hook_event
        with patch("sys.stdin") as stdin, \
                patch("overcode.time_context.generate_enhanced_context", return_value="ctx"):
            stdin.read.return_value = json.dumps(
                {"hook_event_name": "UserPromptSubmit", "session_id": "abc"})
            handle_hook_event()

    def test_permit_lets_one_prompt_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_SESSION_ID", "sid-1")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        self._state(tmp_path)
        budget_gate.write_permit("agents", "sid-1")
        self._prompt()  # let through
        with pytest.raises(SystemExit) as exc:
            self._prompt()  # the permit is spent
        assert exc.value.code == 2


# --- the daemon publishes "rejected" -----------------------------------------

@pytest.fixture
def daemon_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


class TestDaemonBadge:
    def _fleet(self, env, status="waiting_user"):
        from tests.daemon_tick_harness import (FrozenClock, ScriptedDetector, make_daemon,
                                               seed_sessions, seed_steady_state)
        start = datetime(2026, 10, 27, 12, 0, 0)
        sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 1, "agents", env / "work", start)
        detector = ScriptedDetector(lambda tick, s: (status, "Waiting", ""))
        daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        return daemon, detector, sm, sessions[0], FrozenClock(start)

    def _published(self, daemon, sid):
        return next(s for s in daemon.state.sessions if s.session_id == sid)

    def _over(self, sm, sid, rejected=True):
        sm.set_cost_budget(sid, 5.0)
        sm.update_stats(sid, estimated_cost_usd=5.42)
        if rejected:
            sm.update_session(sid, budget_rejected_at="2026-10-27T12:00:00+00:00")

    def test_refused_agent_shows_red_rejected(self, daemon_env):
        from tests.daemon_tick_harness import run_ticks
        daemon, detector, sm, s, clock = self._fleet(daemon_env)
        self._over(sm, s.id)
        with clock.installed():
            run_ticks(daemon, detector, clock, 2)
        view = self._published(daemon, s.id)
        assert view.current_status == "error"
        assert view.current_activity == budget_gate.REJECTED_ACTIVITY
        from overcode.status_constants import STATUS_COLOR_RED
        assert view.status_detail["color"] == STATUS_COLOR_RED
        assert {"kind": "error", "label": "rejected"} in view.status_detail["badges"]

    def test_over_budget_without_a_refusal_is_not_rejected(self, daemon_env):
        from tests.daemon_tick_harness import run_ticks
        daemon, detector, sm, s, clock = self._fleet(daemon_env)
        self._over(sm, s.id, rejected=False)
        with clock.installed():
            run_ticks(daemon, detector, clock, 2)
        assert self._published(daemon, s.id).current_status != "error"

    def test_a_working_agent_is_not_rejected(self, daemon_env):
        from tests.daemon_tick_harness import run_ticks
        daemon, detector, sm, s, clock = self._fleet(daemon_env, status="running")
        self._over(sm, s.id)
        with clock.installed():
            run_ticks(daemon, detector, clock, 2)
        assert self._published(daemon, s.id).current_status == "running"

    def test_raising_the_budget_clears_it(self, daemon_env):
        from tests.daemon_tick_harness import run_ticks
        daemon, detector, sm, s, clock = self._fleet(daemon_env)
        self._over(sm, s.id)
        with clock.installed():
            run_ticks(daemon, detector, clock, 2)
            sm.set_cost_budget(s.id, 50.0)
            run_ticks(daemon, detector, clock, 2)
        assert self._published(daemon, s.id).current_status != "error"
        assert sm.get_session(s.id).budget_rejected_at is None


# --- the TUI's command bar and reinject ---------------------------------------

class TestTui:
    def test_command_bar_notifies_the_refusal(self, rig):
        from overcode.tui_actions.command_bar import CommandBarMixin
        launcher, sm, tmux = rig
        kid = _over_budget(sm, backend="opencode")
        tui = MagicMock()
        tui._resolve_command_bar_session.return_value = sm.get_session(kid.id)
        message = MagicMock(session_id=kid.id, session_name="kid", text="keep going")
        with patch("overcode.tui_actions.command_bar.AgentLauncher", return_value=launcher):
            CommandBarMixin.on_command_bar_send_requested(tui, message)
        assert "keep going" not in _typed(tmux)
        text, = tui.notify.call_args.args
        assert "over its cost budget" in text and "overcode budget set kid" in text
        assert tui.notify.call_args.kwargs["severity"] == "error"
        tui._record_instruction.assert_not_called()

    def test_reinject_notifies_the_refusal(self, rig):
        from overcode.tui_actions.dialogs import DialogsMixin
        from overcode.tui_widgets import SessionSummary
        launcher, sm, tmux = rig
        kid = _over_budget(sm, backend="codex")
        tui = MagicMock()
        tui.focused = MagicMock(spec=SessionSummary)
        tui.focused.session = sm.get_session(kid.id)
        message = MagicMock(text="again")
        with patch("overcode.tui_actions.dialogs.AgentLauncher", return_value=launcher):
            DialogsMixin.on_instruction_history_modal_reinject_requested(tui, message)
        assert "again" not in _typed(tmux)
        assert "over its cost budget" in tui.notify.call_args.args[0]
