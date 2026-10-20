"""Idle stages (idle_stages.py): 📥 → 😴 after idle.drowsy_after, and the
engine's sleep after idle.sleep_after, with the wake that follows work."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from overcode.config import config_path, get_idle_config, save_idle_setting
from overcode.hook_status_detector import synthesize_status_detail_from_legacy
from overcode.idle_stages import (
    format_after, idle_since, parse_after, plain_idle, should_auto_sleep, should_auto_wake,
    staged_detail,
)
from overcode.status_constants import (
    STATUS_COLOR_RED, STATUS_ERROR, STATUS_WAITING_APPROVAL, STATUS_WAITING_USER, StatusBadge,
    StatusDetail,
)
from tests.daemon_tick_harness import (
    FrozenClock, ScriptedDetector, make_daemon, run_ticks, seed_sessions, seed_steady_state,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    """A config.yaml of this test's own: OVERCODE_DIR is shared by the run
    (a TUI test's make_tui gives it one of its own anyway)."""
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))

START = datetime(2026, 10, 20, 12, 0, 0)
IDLE = StatusDetail(STATUS_COLOR_RED, [StatusBadge("awaiting_input")], STATUS_WAITING_USER)


def _write_config(text: str) -> None:
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(text)


class TestDurations:
    @pytest.mark.parametrize("raw,seconds", [
        ("30m", 1800.0), ("2h", 7200.0), ("3d", 259200.0), (90, 90.0), ("45s", 45.0),
        ("never", None), ("off", None), (False, None), ("NEVER", None), (0, None),
    ])
    def test_parse(self, raw, seconds):
        assert parse_after(raw, 1.0) == seconds

    def test_unreadable_is_the_default(self):
        assert parse_after("soon", 3600.0) == 3600.0
        assert parse_after(None, 3600.0) == 3600.0

    @pytest.mark.parametrize("seconds,text", [
        (None, "never"), (600.0, "10m"), (3600.0, "1h"), (86400.0, "1d"), (90.0, "90s"),
    ])
    def test_format(self, seconds, text):
        assert format_after(seconds) == text


class TestConfig:
    def test_defaults_are_1h_and_never(self):
        assert get_idle_config() == {"drowsy_after": 3600.0, "sleep_after": None}

    def test_yaml_values_and_days(self):
        _write_config("idle:\n  drowsy_after: 10m\n  sleep_after: 3d\n")
        assert get_idle_config() == {"drowsy_after": 600.0, "sleep_after": 259200.0}

    def test_never_and_a_bad_value(self, caplog):
        _write_config("idle:\n  drowsy_after: never\n  sleep_after: lots\n")
        assert get_idle_config() == {"drowsy_after": None, "sleep_after": None}
        assert "idle.sleep_after: not a duration" in caplog.text

    def test_the_dialog_saves_and_keeps_the_rest(self):
        _write_config("# mine\nhostname: box\n")
        save_idle_setting("sleep_after", 86400.0)
        save_idle_setting("drowsy_after", None)
        text = config_path().read_text()
        assert "# mine" in text and "hostname: box" in text
        assert get_idle_config() == {"drowsy_after": None, "sleep_after": 86400.0}


class TestWhatIsIdle:
    def test_only_the_awaiting_input_verdict(self):
        assert plain_idle(STATUS_WAITING_USER, IDLE)

    @pytest.mark.parametrize("status,activity", [
        (STATUS_WAITING_APPROVAL, "Permission: approval required"),
        (STATUS_WAITING_APPROVAL, "Question: waiting for your answer"),
        ("waiting_heartbeat", ""), ("waiting_oversight", ""), ("done", ""),
        (STATUS_ERROR, "API error"), ("unknown", ""), ("terminated", ""), ("running", ""),
        ("asleep", ""),
    ])
    def test_nothing_else_is(self, status, activity):
        detail = synthesize_status_detail_from_legacy(status, activity)
        assert not plain_idle(status, detail)
        assert idle_since(status, detail, 100.0) is None

    def test_overdue_is_not_plain_idle(self):
        overdue = StatusDetail(STATUS_COLOR_RED, [StatusBadge("overdue")], STATUS_WAITING_USER)
        assert not plain_idle(STATUS_WAITING_USER, overdue)

    def test_since_is_the_status_start(self):
        assert idle_since(STATUS_WAITING_USER, IDLE, 1234.5) == 1234.5


class TestDrowsy:
    def test_the_badge_turns_after_drowsy_after(self):
        assert staged_detail(IDLE, 1000.0, 1000.0 + 3599, 3600.0).badges[0].kind == "awaiting_input"
        assert staged_detail(IDLE, 1000.0, 1000.0 + 3600, 3600.0).badges[0].kind == "drowsy"

    def test_never_and_no_idle_leave_it(self):
        assert staged_detail(IDLE, 0.0, 1e9, None) is IDLE
        assert staged_detail(IDLE, None, 1e9, 60.0) is IDLE

    def test_only_awaiting_input_turns(self):
        question = StatusDetail("color_orange", [StatusBadge("ask_question")], STATUS_WAITING_APPROVAL)
        assert staged_detail(question, 0.0, 1e9, 60.0) is question

    def test_the_drowsy_cell_is_dim_and_themed(self):
        from overcode.columns.render_identity import _render_status_detail
        from overcode.status_theme import StatusTheme, save_theme

        ctx = SimpleNamespace(bg="", emoji_free=False)
        drowsy = staged_detail(IDLE, 0.0, 1e9, 60.0)
        text, style = _render_status_detail(ctx, drowsy)[0]
        assert "😴" in text and style == "dim"
        assert "📥" in _render_status_detail(ctx, IDLE)[0][0]
        ctx.emoji_free = True
        assert "zz" in _render_status_detail(ctx, drowsy)[0][0]
        ctx.emoji_free = False
        save_theme(StatusTheme("shapes", {"drowsy": "🔕"}))
        assert "🔕" in _render_status_detail(ctx, drowsy)[0][0]
        assert "◇" in _render_status_detail(ctx, IDLE)[0][0]


class TestSleepRules:
    def _s(self, **kw):
        base = dict(is_asleep=False, asleep_auto=False, parent_session_id=None,
                    heartbeat_enabled=False, heartbeat_paused=False)
        return SimpleNamespace(**{**base, **kw})

    def test_a_root_idle_long_enough(self):
        assert should_auto_sleep(self._s(), 0.0, 100.0, 100.0)
        assert not should_auto_sleep(self._s(), 0.0, 99.0, 100.0)
        assert not should_auto_sleep(self._s(), 0.0, 1e9, None)
        assert not should_auto_sleep(self._s(), None, 1e9, 1.0)

    def test_never_a_child_a_sleeper_or_a_heartbeat(self):
        assert not should_auto_sleep(self._s(parent_session_id="p"), 0.0, 1e9, 1.0)
        assert not should_auto_sleep(self._s(is_asleep=True), 0.0, 1e9, 1.0)
        assert not should_auto_sleep(self._s(heartbeat_enabled=True), 0.0, 1e9, 1.0)
        assert should_auto_sleep(self._s(heartbeat_enabled=True, heartbeat_paused=True), 0.0, 1e9, 1.0)

    def test_only_an_auto_sleep_wakes_by_itself(self):
        assert should_auto_wake(self._s(is_asleep=True, asleep_auto=True), "running")
        assert should_auto_wake(self._s(is_asleep=True, asleep_auto=True), STATUS_WAITING_APPROVAL)
        assert not should_auto_wake(self._s(is_asleep=True, asleep_auto=True), STATUS_WAITING_USER)
        assert not should_auto_wake(self._s(is_asleep=True), "running")


# ── through whole daemon ticks ───────────────────────────────────────────


class Engine:
    attended = True
    burn_windows: set = set()

    def ring(self, agent, episode):
        pass

    def publish(self, snapshot):
        pass

    def take_visits(self):
        return []

    def beat(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


def _fleet(env, script):
    from overcode.session_manager import SessionManager

    sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, 2, "agents", env / "work", START)
    detector = ScriptedDetector(script)
    daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, START)
    daemon._engine = Engine()
    return daemon, detector, sm, sessions, FrozenClock(START)


def _state(daemon, sid):
    return next(s for s in daemon.state.sessions if s.session_id == sid)


class TestThroughTheDaemon:
    def test_idle_since_is_published_and_holds_still(self, env):
        daemon, detector, sm, sessions, clock = _fleet(
            env, lambda t, s: ("waiting_user", "Waiting for user input", ""))
        sid = sessions[1].id  # waiting_user since START - 5 min
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
            first = _state(daemon, sid).idle_since
            run_ticks(daemon, detector, clock, 5)
        assert first == (START.timestamp() - 300)
        assert _state(daemon, sid).idle_since == first
        assert not sm.get_session(sid).is_asleep  # sleep_after: never

    def test_an_approval_has_no_idle_since(self, env):
        daemon, detector, _, sessions, clock = _fleet(
            env, lambda t, s: ("waiting_approval", "Question: waiting for your answer", ""))
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
        assert all(s.idle_since is None for s in daemon.state.sessions)

    def test_a_long_idle_sleeps_and_wakes_when_it_works(self, env):
        _write_config("idle:\n  sleep_after: 4m\n")
        working = [False]

        def script(t, s):
            if working[0]:
                return ("running", "Using Read", "")
            return ("waiting_user", "Waiting for user input", "")

        daemon, detector, sm, sessions, clock = _fleet(env, script)
        idle_long, idle_new = sessions[1].id, sessions[0].id  # 5 min idle / was running
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
            slept = sm.get_session(idle_long)
            assert slept.is_asleep and slept.asleep_auto
            assert not sm.get_session(idle_new).is_asleep  # idle only from now
            run_ticks(daemon, detector, clock, 2)
            assert _state(daemon, idle_long).current_status == "asleep"
            assert _state(daemon, idle_long).asleep_auto
            working[0] = True
            run_ticks(daemon, detector, clock, 2)
        woke = sm.get_session(idle_long)
        assert not woke.is_asleep and not woke.asleep_auto
        assert _state(daemon, idle_long).current_status == "running"

    def test_a_manual_sleep_stays_when_it_works(self, env):
        daemon, detector, sm, sessions, clock = _fleet(env, lambda t, s: ("running", "Using Read", ""))
        sid = sessions[0].id
        sm.update_session(sid, is_asleep=True, asleep_auto=False)
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
        assert sm.get_session(sid).is_asleep
        assert _state(daemon, sid).current_status == "asleep"

    def test_a_heartbeat_agent_is_never_put_to_sleep(self, env):
        _write_config("idle:\n  sleep_after: 1m\n")
        daemon, detector, sm, sessions, clock = _fleet(
            env, lambda t, s: ("waiting_user", "Waiting for user input", ""))
        sid = sessions[1].id
        sm.update_session(sid, heartbeat_enabled=True, heartbeat_instruction="go on",
                          heartbeat_frequency_seconds=3600)
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
        assert not sm.get_session(sid).is_asleep


# ── in the TUI ───────────────────────────────────────────────────────────


def _idle_view(name, i, idle_at, **fields):
    from tests.unit.test_swarm_tui import _view
    base = dict(current_status="waiting_user", live_colour="color_red",
                episode_colour="color_red", idle_since=idle_at,
                status_detail={"color": "color_red", "badges": [{"kind": "awaiting_input"}],
                               "legacy_status": "waiting_user"})
    return _view(name, i, **{**base, **fields})


def _publish(engine, *views):
    from overcode.engine_protocol import Snapshot
    engine.publish(Snapshot(agents={v["session_id"]: v for v in views},
                            fleet={"status": "active", "current_interval": 2,
                                   "interval_mode": "attended"}))


class TestInTheTui:
    async def test_a_long_idle_row_shows_drowsy(self, make_tui):
        import time
        from tests.tui_harness import FakeEngine, ready, three_agents
        engine = FakeEngine()
        app = make_tui(three_agents(), engine=engine, size=(200, 40))
        async with app.run_test() as pilot:
            await ready(pilot)
            now = time.time()
            _publish(engine, _idle_view("alpha", 0, now - 7200), _idle_view("bravo", 1, now - 60))
            await pilot.pause()
            rows = {w.session.name: w for w in app._get_widgets_in_session_order()}
            assert rows["alpha"].status_detail.badges[0].kind == "drowsy"
            assert rows["bravo"].status_detail.badges[0].kind == "awaiting_input"
            assert "😴" in rows["alpha"].render().plain
            assert "📥" in rows["bravo"].render().plain

    async def test_an_engine_sleep_shows_at_once_with_a_toast_and_its_wake(self, make_tui):
        from tests.tui_harness import FakeEngine, ready, three_agents
        engine = FakeEngine()
        app = make_tui(three_agents(), engine=engine, size=(200, 40))
        notes = []
        async with app.run_test() as pilot:
            await ready(pilot)
            app.notify = lambda msg, **kw: notes.append(msg)
            _publish(engine, _idle_view("alpha", 0, 0.0))
            await pilot.pause()
            row = next(w for w in app._get_widgets_in_session_order() if w.session.name == "alpha")
            assert not row.session.is_asleep
            _publish(engine, _idle_view("alpha", 0, None, current_status="asleep",
                                        is_asleep=True, asleep_auto=True))
            await pilot.pause()
            assert row.session.is_asleep and row.detected_status == "asleep"
            assert any("fell asleep" in n for n in notes)
            _publish(engine, _idle_view("alpha", 0, None, current_status="running",
                                        is_asleep=False, asleep_auto=False))
            await pilot.pause()
            assert not row.session.is_asleep and row.detected_status == "running"
