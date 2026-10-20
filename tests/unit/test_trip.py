"""Trip counters (trip.py): a row's spend since a reset; the totals keep counting."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overcode import trip
from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
from overcode.session_manager import Session, SessionManager, SessionStats

pytestmark = pytest.mark.unit

WEEK = 7 * 86400.0
RESET = "2026-10-20T12:00:00+00:00"
RESET_AT = datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()


def _session(base=None, reset_at=RESET, **kw):
    return SimpleNamespace(id="s1", trip_base=base, trip_reset_at=reset_at, **kw)


def _totals(cost=5.0, energy=900.0, inp=1000, out=500, cw=40, cr=60):
    return SimpleNamespace(estimated_cost_usd=cost, estimated_energy_j=energy, input_tokens=inp,
                           output_tokens=out, cache_creation_tokens=cw, cache_read_tokens=cr)


# -- the arithmetic --------------------------------------------------------

class TestTripView:
    def test_never_reset_is_none(self):
        assert trip.trip_view(_session(None, None), _totals()) is None
        assert trip.trip_view(SimpleNamespace(), _totals()) is None

    def test_spend_since_the_base(self):
        base = trip.base_of(_totals(cost=2.0, energy=400.0, inp=600, out=100, cw=40, cr=10))
        view = trip.trip_view(_session(base), _totals())
        assert view == {"reset_at": RESET, "estimated_cost_usd": 3.0, "estimated_energy_j": 500.0,
                        "input_tokens": 400, "output_tokens": 400, "cache_creation_tokens": 0,
                        "cache_read_tokens": 50}
        assert trip.trip_tokens(view) == 800

    def test_a_total_under_the_base_reads_zero(self):
        """A recomputed total can come in under the base: never negative."""
        base = trip.base_of(_totals(cost=9.0))
        assert trip.trip_view(_session(base), _totals(cost=5.0))["estimated_cost_usd"] == 0.0

    def test_base_reads_a_stats_dict_and_ignores_junk(self):
        base = trip.base_of({"estimated_cost_usd": 1.5, "input_tokens": "x", "output_tokens": True})
        assert base["estimated_cost_usd"] == 1.5 and base["input_tokens"] == 0.0
        assert base["output_tokens"] == 0.0 and "pp7d" not in base


class TestQuotaWeek:
    week = RESET_AT - 86400  # the quota week began a day before the reset

    def _base(self, pp=4.0, week=None):
        return trip.base_of(_totals(), {"pp7d": pp}, self.week if week is None else week)

    def test_same_week_subtracts_the_points_at_the_reset(self):
        view = trip.trip_view(_session(self._base()), _totals(), {"pp7d": 6.5}, self.week)
        assert view["pp7d"] == 2.5

    def test_a_rolled_over_week_is_this_weeks_points(self):
        view = trip.trip_view(_session(self._base()), _totals(), {"pp7d": 1.25}, self.week + WEEK)
        assert view["pp7d"] == 1.25

    def test_a_drifting_reset_time_is_still_the_same_week(self):
        view = trip.trip_view(_session(self._base()), _totals(), {"pp7d": 6.0}, self.week + 30)
        assert view["pp7d"] == 2.0

    def test_a_base_without_a_week_goes_by_the_reset_time(self):
        base = trip.base_of(_totals(), {"pp7d": 4.0}, None)
        assert trip.trip_view(_session(base), _totals(), {"pp7d": 6.0}, self.week)["pp7d"] == 2.0
        later = RESET_AT + 3600
        assert trip.trip_view(_session(base), _totals(), {"pp7d": 6.0}, later)["pp7d"] == 6.0

    def test_no_reading_now_keeps_the_base(self):
        view = trip.trip_view(_session(self._base()), _totals(), {"pp7d": 5.0}, None)
        assert view["pp7d"] == 1.0

    def test_no_quota_view_publishes_no_points(self):
        assert "pp7d" not in trip.trip_view(_session(self._base()), _totals(), None, self.week)

    def test_week_start_from_the_usage_reading(self):
        now = RESET_AT
        assert trip.week_start({"r7": now + 3600}, now) == now + 3600 - WEEK
        assert trip.week_start({"r7": now - WEEK - 1}, now) is None  # an old reading
        assert trip.week_start(None, now) is None


def test_subtree_trip_cost_is_the_rows_sum_while_one_was_reset():
    a = SimpleNamespace(trip={"estimated_cost_usd": 0.5}, estimated_cost_usd=4.0)
    b = SimpleNamespace(trip=None, estimated_cost_usd=2.0)
    c = SimpleNamespace(trip=None, estimated_cost_usd=1.0)
    assert trip.subtree_trip_cost([a, b, c]) == 3.5
    assert trip.subtree_trip_cost([b, c]) is None


# -- the record --------------------------------------------------------------

@pytest.fixture
def sm(tmp_path):
    return SessionManager(state_dir=tmp_path / "state", skip_git_detection=True)


def _agent(sm, name, parent=None):
    s = sm.create_session(name=name, tmux_session="test", tmux_window=f"{name}-w",
                          command=["claude"], start_directory="/tmp")
    if parent:
        sm.update_session(s.id, parent_session_id=parent)
    return s


class TestRecord:
    def test_old_records_load_without_a_trip(self):
        s = Session.from_dict({"id": "a", "name": "a", "tmux_session": "t", "tmux_window": "w",
                               "command": ["claude"], "start_directory": "/tmp",
                               "start_time": RESET, "stats": {}})
        assert s is not None and s.trip_base is None and s.trip_reset_at is None

    def test_a_record_with_a_trip_round_trips(self):
        s = Session.from_dict({"id": "a", "name": "a", "tmux_session": "t", "tmux_window": "w",
                               "command": ["claude"], "start_directory": "/tmp",
                               "start_time": RESET, "trip_base": {"estimated_cost_usd": 1.0},
                               "trip_reset_at": RESET, "trip_future_key": 1})
        assert Session.from_dict(s.to_dict()).trip_base == {"estimated_cost_usd": 1.0}

    def test_reset_trips_stores_the_base_and_keeps_the_totals(self, sm):
        a = _agent(sm, "alpha")
        sm.update_stats(a.id, estimated_cost_usd=3.0, input_tokens=100, output_tokens=50)
        assert sm.reset_trips([a.id, "gone", a.id], RESET, {a.id: ({"pp7d": 2.0}, 123.0)}) \
            == [a.id]
        got = sm.get_session(a.id)
        assert got.trip_reset_at == RESET
        assert got.trip_base["estimated_cost_usd"] == 3.0 and got.trip_base["input_tokens"] == 100
        assert got.trip_base["pp7d"] == 2.0 and got.trip_base["quota_week"] == 123.0
        assert got.stats.estimated_cost_usd == 3.0  # the odometer
        assert trip.trip_view(got, got.stats)["estimated_cost_usd"] == 0.0

    def test_reset_reads_the_engine_quota_and_wakes_it(self, sm):
        a = _agent(sm, "alpha")
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        state = MonitorDaemonState(
            sessions=[SessionDaemonState(session_id=a.id, quota={"pp7d": 1.5})],
            usage={"r7": now.timestamp() + 86400})
        with patch("overcode.monitor_daemon_state.get_monitor_daemon_state", return_value=state), \
                patch("overcode.settings.signal_activity") as wake:
            assert trip.reset(sm, [a.id], "test", now=now) == [a.id]
        wake.assert_called_once_with("test")
        base = sm.get_session(a.id).trip_base
        assert base["pp7d"] == 1.5 and base["quota_week"] == now.timestamp() + 86400 - WEEK

    def test_tree_ids_are_the_agent_and_its_descendants(self, sm):
        a = _agent(sm, "alpha")
        b = _agent(sm, "bravo", parent=a.id)
        c = _agent(sm, "charlie", parent=b.id)
        _agent(sm, "delta")
        assert set(trip.tree_ids(sm, a.id)) == {a.id, b.id, c.id}


# -- the engine ----------------------------------------------------------------

class TestDaemonPublishes:
    def _daemon(self, usage=None, quota_view=None):
        from overcode.monitor_daemon import MonitorDaemon
        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon._git_by_dir = {}
        daemon._burn_by_session = {}
        daemon.quota = SimpleNamespace(agent_view=lambda sid: quota_view, fleet_usage=usage)
        daemon.clock = SimpleNamespace(wall=lambda: datetime.fromtimestamp(RESET_AT + 60))
        return daemon

    def _session(self, base, reset_at=RESET):
        return SimpleNamespace(id="s1", start_directory=None, focal_repo_subdir=None,
                               trip_base=base, trip_reset_at=reset_at)

    def test_the_trip_beside_the_totals(self):
        daemon = self._daemon()
        state = SessionDaemonState(session_id="s1", estimated_cost_usd=5.0, input_tokens=900)
        daemon._attach_git_and_burn(
            self._session(trip.base_of({"estimated_cost_usd": 2.0, "input_tokens": 100})), state)
        assert state.trip["estimated_cost_usd"] == 3.0 and state.trip["input_tokens"] == 800
        assert state.estimated_cost_usd == 5.0

    def test_q_points_this_week(self):
        week = RESET_AT - 3600
        daemon = self._daemon(usage={"r7": week + WEEK}, quota_view={"pp7d": 3.0})
        state = SessionDaemonState(session_id="s1")
        base = trip.base_of({}, {"pp7d": 1.0}, week)
        daemon._attach_git_and_burn(self._session(base), state)
        assert state.trip["pp7d"] == 2.0

    def test_never_reset_publishes_none(self):
        state = SessionDaemonState(session_id="s1")
        self._daemon()._attach_git_and_burn(self._session(None, None), state)
        assert state.trip is None

    def test_subtree_on_trips(self):
        from overcode.monitor_daemon import MonitorDaemon
        parent = SessionDaemonState(session_id="p", name="p", estimated_cost_usd=4.0,
                                    trip={"estimated_cost_usd": 0.5})
        kid = SessionDaemonState(session_id="k", name="k", parent_name="p",
                                 estimated_cost_usd=2.0)
        MonitorDaemon._compute_subtree_costs(None, [parent, kid])
        assert parent.subtree_cost_usd == 6.0 and parent.subtree_trip_cost_usd == 2.5
        parent.trip = None
        MonitorDaemon._compute_subtree_costs(None, [parent, kid])
        assert parent.subtree_trip_cost_usd is None


def test_a_reset_fleet_still_publishes_no_deltas_while_quiet(tmp_path, monkeypatch):
    """The trip holds still between ticks: a quiet fleet sends no deltas."""
    from overcode.engine_protocol import diff
    from tests.daemon_tick_harness import (FrozenClock, ScriptedDetector, make_daemon, run_ticks,
                                           seed_sessions, seed_steady_state)

    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    start = datetime(2026, 10, 7, 12, 0, 0)
    sm = SessionManager(state_dir=tmp_path / "state" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, 2, "agents", tmp_path / "work", start)
    sm.reset_trips([s.id for s in sessions], RESET)
    detector = ScriptedDetector(lambda tick, s: ("waiting_user", "Waiting", ""))
    daemon = make_daemon(tmp_path / "state", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, start)
    clock = FrozenClock(start)
    with clock.installed():
        run_ticks(daemon, detector, clock, 4)
        first = daemon.state.to_engine_snapshot(clock.now.timestamp())
        run_ticks(daemon, detector, clock, 6)
        second = daemon.state.to_engine_snapshot(clock.now.timestamp())
    for s in sessions:
        assert first.agents[s.id]["trip"]["reset_at"] == RESET
    delta = diff(first, second)
    assert delta.empty, (delta.set, delta.fleet_set)


# -- the cells ---------------------------------------------------------------

def _ctx(trip_view=None, *, cost=4.0, budget=0.0, quota=None, emoji_free=False, show_cost="cost",
         subtree=0.0, subtree_trip=None, sub=None):
    from overcode.history_reader import AgentSessionStats
    from overcode.summary_columns import build_cli_context
    session = SimpleNamespace(
        id="s1", name="alpha", start_time=None, is_asleep=False, permissiveness_mode="normal",
        repo_name="r", branch="b", cost_budget_usd=budget, model="", effort="",
        stats=SessionStats(estimated_cost_usd=cost, estimated_energy_j=5.0e5,
                           input_tokens=9000, output_tokens=1000))
    claude = AgentSessionStats(interaction_count=1, input_tokens=9000, output_tokens=1000,
                               cache_creation_tokens=0, cache_read_tokens=0, work_times=[])
    ctx = build_cli_context(session, session.stats, claude, None, "running", 0, 0,
                            emoji_free=emoji_free, quota=quota, trip=trip_view,
                            subtree_cost_usd=subtree, subtree_trip_cost_usd=subtree_trip)
    ctx.show_cost = show_cost
    ctx.subagent_cost_usd = sub
    return ctx


TRIP = {"reset_at": RESET, "estimated_cost_usd": 0.25, "estimated_energy_j": 900.0,
        "input_tokens": 100, "output_tokens": 20, "cache_creation_tokens": 0,
        "cache_read_tokens": 0, "pp7d": 0.5}


def _text(segments):
    return "".join(t for t, _ in segments)


class TestCells:
    @pytest.mark.parametrize("col", ["token_count", "cost", "joules", "quota"])
    def test_trip_cells_are_marked_and_keep_the_width(self, col):
        from rich.cells import cell_len
        from overcode.summary_columns import COLUMNS_BY_ID
        render = COLUMNS_BY_ID[col].render
        quota = {"pp7d": 12.34, "conf": "low"}
        plain, reset = _text(render(_ctx(quota=quota))), _text(render(_ctx(TRIP, quota=quota)))
        assert reset.startswith("↺") and not plain.startswith("↺")
        assert cell_len(plain) == cell_len(reset)
        ascii_ = _text(render(_ctx(TRIP, quota=quota, emoji_free=True)))
        assert ascii_.startswith("*") and cell_len(ascii_) == cell_len(plain)

    def test_the_values_are_the_trips(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        ctx = _ctx(TRIP, quota={"pp7d": 12.34})
        assert "$0.25" in _text(COLUMNS_BY_ID["cost"].render(ctx))
        assert "120" in _text(COLUMNS_BY_ID["token_count"].render(ctx))
        assert "0.50%" in _text(COLUMNS_BY_ID["quota"].render(ctx))
        assert COLUMNS_BY_ID["cost"].sort_key(ctx) == 0.25
        assert COLUMNS_BY_ID["cost"].render_plain(ctx) == "↺$0.25"
        assert COLUMNS_BY_ID["token_count"].render_plain(ctx) == "↺Σ 120"
        assert COLUMNS_BY_ID["quota"].render_plain(ctx) == "↺0.50% wk"

    def test_budget_colours_follow_the_total(self):
        """A reset never gives more budget: $4 of a $4 budget is red on a $0.25 trip."""
        from overcode.summary_columns import COLUMNS_BY_ID
        (_, style), = COLUMNS_BY_ID["cost"].render(_ctx(TRIP, cost=4.0, budget=4.0))
        assert "red" in style
        assert COLUMNS_BY_ID["cost"].render_plain(_ctx(TRIP, budget=4.0)) == "↺$0.25/$4.00"

    def test_subtree_on_trips(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        col = COLUMNS_BY_ID["subtree_cost"]
        assert _text(col.render(_ctx(subtree=6.0))).startswith(" Σ")
        reset = _ctx(subtree=6.0, subtree_trip=2.5)
        assert _text(col.render(reset)).startswith("↺Σ") and "$2.50" in _text(col.render(reset))
        assert col.render_plain(reset) == "↺Σ$2.50"
        hover = col.hover(reset).plain
        assert "$2.50" in hover and "$6.00 in all" in hover and "never adds budget" in hover
        assert col.hover(_ctx(subtree=6.0)) is None


class TestHover:
    def test_spend_hover_shows_trip_total_and_when(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        text = COLUMNS_BY_ID["cost"].hover(_ctx(TRIP)).plain
        assert "↺" in text and "$0.25" in text and "$4.00" in text and "in all" in text
        assert "since" in text and "never adds budget" in text

    def test_with_delegated_work_too(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        text = COLUMNS_BY_ID["cost"].hover(_ctx(TRIP, sub=1.5)).plain
        assert "$1.50 its subagents" in text and "$2.50 its own turns" in text

    def test_no_trip_no_delegation_no_hover(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        assert COLUMNS_BY_ID["cost"].hover(_ctx()) is None
        assert COLUMNS_BY_ID["joules"].hover(_ctx(TRIP)) is not None

    def test_quota_hover_has_the_trip_and_this_week(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        text = COLUMNS_BY_ID["quota"].hover(_ctx(TRIP, quota={"pp7d": 12.34})).plain
        assert "0.50%" in text and "12.34% of the weekly quota this week" in text

    def test_describe(self):
        assert trip.describe(None) == ""
        today = datetime.now().astimezone().replace(hour=9, minute=5, second=0, microsecond=0)
        assert trip.describe({"reset_at": today.isoformat()}) == "since 09:05"
        assert trip.describe({"reset_at": "2020-03-04T10:00:00"}) == "since 4 Mar 10:00"


# -- the TUI -------------------------------------------------------------------

def test_the_palette_lists_the_three_resets():
    from overcode.command_palette import COMMANDS
    entries = {c.action: c for c in COMMANDS}
    assert entries["reset_trip"].agent and entries["reset_trip_tree"].agent
    assert not entries["reset_trip_all"].agent


@pytest.mark.asyncio
class TestResetFromTheTui:
    async def _run(self, make_tui, action, focus="alpha"):
        sm = SessionManager(skip_git_detection=True)
        a = _agent(sm, "alpha")
        b = _agent(sm, "bravo", parent=a.id)
        c = _agent(sm, "charlie")
        for s in (a, b, c):
            sm.update_stats(s.id, estimated_cost_usd=2.0)
        app = make_tui()
        notes = []
        with patch("overcode.settings.signal_activity") as wake:
            async with app.run_test(size=(200, 30)) as pilot:
                await pilot.pause()
                app.notify = lambda msg, **kw: notes.append(msg)
                widget = next(w for w in app._get_widgets_in_session_order()
                              if w.session.name == focus)
                widget.focus()
                await pilot.pause()
                getattr(app, f"action_{action}")()
                await pilot.pause()
        reset = {s.name for s in sm.list_sessions() if s.trip_reset_at}
        return reset, notes, wake

    async def test_reset_the_focused_agent(self, make_tui):
        reset, notes, wake = await self._run(make_tui, "reset_trip")
        assert reset == {"alpha"} and wake.called
        assert any("Trip reset for 'alpha'" in n and "budgets keep counting" in n for n in notes)

    async def test_reset_its_tree(self, make_tui):
        reset, notes, _ = await self._run(make_tui, "reset_trip_tree")
        assert reset == {"alpha", "bravo"}
        assert any("and 1 under it" in n for n in notes)

    async def test_reset_all(self, make_tui):
        reset, notes, _ = await self._run(make_tui, "reset_trip_all")
        assert reset == {"alpha", "bravo", "charlie"}
        assert any("3 agents" in n for n in notes)

    async def test_a_remote_agent_is_refused_with_how_to_do_it(self, make_tui, monkeypatch):
        from overcode.tui_actions import session as session_actions

        remote = SimpleNamespace(id="remote:h:a", name="a", is_remote=True, source_host="h")
        monkeypatch.setattr(session_actions, "_get_focused_session",
                            lambda tui: SimpleNamespace(session=remote))
        app = make_tui(sessions=[])
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda msg, **kw: notes.append(msg)
            app.action_reset_trip()
        assert any("overcode trip reset a" in n for n in notes)


# -- the CLI -------------------------------------------------------------------

class TestCli:
    @pytest.fixture
    def cli(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "s"))
        sm = SessionManager(skip_git_detection=True)
        monkeypatch.setattr("overcode.monitor_daemon_state.get_monitor_daemon_state",
                            lambda session=None: None)
        monkeypatch.setattr("overcode.settings.signal_activity", lambda session=None: None)
        runner = CliRunner()
        return sm, lambda *args: runner.invoke(app, list(args))

    def _fleet(self, sm):
        a = _agent(sm, "alpha")
        b = _agent(sm, "bravo", parent=a.id)
        _agent(sm, "charlie")
        for s in sm.list_sessions():
            sm.update_stats(s.id, estimated_cost_usd=2.0, input_tokens=100, output_tokens=50)
        return a, b

    def test_reset_one_tree_and_all(self, cli):
        sm, run = cli
        self._fleet(sm)
        r = run("trip", "reset", "alpha", "--session", "test")
        assert r.exit_code == 0, r.output
        assert {s.name for s in sm.list_sessions() if s.trip_reset_at} == {"alpha"}
        assert "keep counting" in r.output
        assert run("trip", "reset", "alpha", "--tree", "--session", "test").exit_code == 0
        assert {s.name for s in sm.list_sessions() if s.trip_reset_at} == {"alpha", "bravo"}
        assert run("trip", "reset", "--all", "--session", "test").exit_code == 0
        assert all(s.trip_reset_at for s in sm.list_sessions())

    def test_reset_needs_a_name_or_all_not_both(self, cli):
        sm, run = cli
        self._fleet(sm)
        assert run("trip", "reset", "--session", "test").exit_code == 1
        assert run("trip", "reset", "alpha", "--all", "--session", "test").exit_code == 1
        assert run("trip", "reset", "nobody", "--session", "test").exit_code == 1

    def test_show(self, cli):
        sm, run = cli
        a, _ = self._fleet(sm)
        r = run("trip", "show", "alpha", "--session", "test")
        assert r.exit_code == 0 and "never reset" in r.output and "$2.00" in r.output
        sm.reset_trips([a.id], RESET)
        sm.update_stats(a.id, estimated_cost_usd=2.5, input_tokens=300)
        r = run("trip", "show", "alpha", "--json", "--session", "test")
        data = json.loads(r.output)
        assert data["trip"]["estimated_cost_usd"] == 0.5 and data["trip"]["input_tokens"] == 200
        assert data["total"]["estimated_cost_usd"] == 2.5 and data["trip_reset_at"] == RESET
        r = run("trip", "show", "alpha", "--session", "test")
        assert "↺ Trip" in r.output and "$0.50" in r.output and "never adds budget" in r.output

    def test_list_json_has_trip_and_total(self, cli, monkeypatch):
        sm, run = cli
        a, _ = self._fleet(sm)
        sm.reset_trips([a.id], RESET)
        monkeypatch.setattr("overcode.cli.agent._reconcile_without_daemon", lambda *a: None)
        r = run("list", "--json", "--session", "test")
        assert r.exit_code == 0, r.output
        rows = {row["name"]: row for row in json.loads(r.output)}
        assert rows["alpha"]["spend"]["trip"]["estimated_cost_usd"] == 0.0
        assert rows["alpha"]["spend"]["total"]["estimated_cost_usd"] == 2.0
        assert rows["charlie"]["spend"]["trip"] is None
