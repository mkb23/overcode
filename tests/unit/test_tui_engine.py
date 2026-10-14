"""tui_engine: reading the engine's agent views into what a row draws (0.6.0)."""

from datetime import datetime

import pytest

from overcode import tui_engine
from overcode.session_manager import SessionStats

pytestmark = pytest.mark.unit


class TestSessionStats:
    def test_engine_values_win_and_the_base_is_untouched(self):
        base = SessionStats(estimated_cost_usd=1.0, current_task="old", steers_count=1)
        view = {"estimated_cost_usd": 2.0, "current_activity": "new", "steers_count": 4,
                "current_status": "waiting_user", "status_since": "2026-10-07T12:00:00",
                "time_base": [10.0, 20.0, 3.0], "time_base_at": 1_000.0}
        out = tui_engine.session_stats(base, view)
        assert (out.estimated_cost_usd, out.current_task, out.steers_count) == (2.0, "new", 4)
        assert out.current_state == "waiting_user"
        assert out.state_since == "2026-10-07T12:00:00"
        assert (out.green_time_seconds, out.non_green_time_seconds, out.sleep_time_seconds) \
            == (10.0, 20.0, 3.0)
        assert out.last_time_accumulation == datetime.fromtimestamp(1_000.0).astimezone().isoformat()
        assert base.estimated_cost_usd == 1.0 and base.current_task == "old"

    def test_no_view_is_the_base(self):
        base = SessionStats()
        assert tui_engine.session_stats(base, {}) is base

    def test_time_in_state_columns_extrapolate_from_the_base(self):
        from overcode.tui_helpers import get_current_state_times

        out = tui_engine.session_stats(SessionStats(), {
            "current_status": "running", "time_base": [100.0, 50.0, 0.0],
            "time_base_at": datetime(2026, 10, 7, 12, 0, 0).timestamp(),
        })
        green, non_green, _ = get_current_state_times(out, now=datetime(2026, 10, 7, 12, 0, 30))
        assert (green, non_green) == (130.0, 50.0)


class TestAgentStats:
    def test_none_without_stats(self):
        assert tui_engine.agent_stats({}) is None
        assert tui_engine.agent_stats({"stats_available": False}) is None
        assert tui_engine.agent_stats({"stats_available": None}) is None

    def test_context_window_is_the_engines(self):
        stats = tui_engine.agent_stats({"stats_available": True, "context_window": 400_000,
                                        "current_context_tokens": 100_000, "model": "x-unknown"})
        assert stats.max_context_tokens == 400_000


class TestStatusDetail:
    def test_badges_count_down_and_never_below_zero(self):
        view = {"status_detail": {"color": "yellow", "legacy_status": "waiting_user", "badges": [
            {"kind": "schedule_wakeup", "eta_at": 1_100},
            {"kind": "monitor", "count": 2},
            {"kind": "tool", "label": "Bash"},
        ]}}
        detail = tui_engine.status_detail(view, now=1_000.0)
        assert [b.eta_seconds for b in detail.badges] == [100.0, None, None]
        assert detail.badges[1].count == 2 and detail.badges[2].label == "Bash"
        assert tui_engine.status_detail(view, now=2_000.0).badges[0].eta_seconds == 0.0

    def test_no_detail(self):
        assert tui_engine.status_detail({}, 0) is None
        assert tui_engine.status_detail({"status_detail": None}, 0) is None


class TestStatusChangedAt:
    @pytest.mark.parametrize("status", ["asleep", "unknown"])
    def test_lifecycle_states_count_from_status_since(self, status):
        view = {"current_status": status, "status_since": "2026-10-07T11:00:00",
                "episode_start": 5.0, "live_colour": "red", "episode_colour": "red"}
        assert tui_engine.status_changed_at(view) == datetime(2026, 10, 7, 11, 0, 0)

    def test_without_an_episode_falls_back_to_status_since(self):
        view = {"current_status": "running", "status_since": "2026-10-07T11:00:00"}
        assert tui_engine.status_changed_at(view) == datetime(2026, 10, 7, 11, 0, 0)


class TestBurn:
    def _view(self, cost):
        return {"burn": {"0.25": {"input_tokens": 100, "output_tokens": 50,
                                  "cost_usd": cost, "energy_j": 900.0}}}

    def test_keys_match_what_the_engine_writes(self):
        # monitor_daemon._sync_burn keys by str(float(hours))
        assert tui_engine.burn_key(15 / 60) == str(float(0.25))
        assert tui_engine.burn_key(1) == "1.0"

    def test_fleet_burn_sums_the_agents(self):
        total = tui_engine.fleet_burn({"a": self._view(1.0), "b": self._view(2.0), "c": {}}, 0.25)
        assert total.window_hours == 0.25
        assert total.total_tokens == 300
        assert total.cost_usd == 3.0
        assert total.cost_per_hour == 12.0
        assert set(total.per_session) == {"a", "b"}

    def test_no_window_no_burn(self):
        assert tui_engine.fleet_burn({"a": self._view(1.0)}, 0) is None
        assert tui_engine.window_burn(self._view(1.0), 0.5) is None


class TestUnvisited:
    @pytest.mark.parametrize("view, here, expected", [
        ({}, None, False),
        ({"input_needed_since": 10.0}, None, True),  # never visited
        ({"input_needed_since": 10.0, "visited_at": 5.0}, None, True),
        ({"input_needed_since": 10.0, "visited_at": 15.0}, None, False),
        ({"input_needed_since": 10.0, "visited_at": 5.0}, 12.0, False),
    ])
    def test_cases(self, view, here, expected):
        assert tui_engine.is_unvisited(view, here) is expected


class TestMonitorState:
    def test_the_status_bar_state_from_a_snapshot(self):
        state = tui_engine.monitor_state(
            {"status": "active", "current_interval": 2, "presence_idle_since": 100,
             "interval_mode": "unattended", "slow_tick_seconds": 3.1},
            {"s1": {"session_id": "s1", "name": "a", "current_status": "running",
                    "estimated_cost_usd": 1.5, "some_future_field": 1}},
        )
        assert state.status == "active" and state.interval_mode == "unattended"
        assert state.slow_tick_seconds == 3.1
        # Only what the bar draws is carried
        assert [(s.session_id, s.current_status, s.estimated_cost_usd, s.name)
                for s in state.sessions] == [("s1", "running", 1.5, "")]

    def test_status_bar_inputs_ignore_fields_the_bar_does_not_draw(self):
        fleet = {"status": "active"}
        view = {"session_id": "s1", "current_status": "running", "input_tokens": 5,
                "current_activity": "reading", "burn": {"1.0": {"cost_usd": 1.0}}}
        before = tui_engine.status_bar_inputs(fleet, {"s1": view}, 1.0)
        same = tui_engine.status_bar_inputs(
            fleet, {"s1": {**view, "current_activity": "writing", "cpu_percent": 9}}, 1.0)
        assert same == before
        for changed in (
            tui_engine.status_bar_inputs({"status": "idle"}, {"s1": view}, 1.0),
            tui_engine.status_bar_inputs(fleet, {"s1": {**view, "input_tokens": 6}}, 1.0),
            tui_engine.status_bar_inputs(
                fleet, {"s1": {**view, "burn": {"1.0": {"cost_usd": 2.0}}}}, 1.0),
            tui_engine.status_bar_inputs(fleet, {"s1": view}, 2.0),
            tui_engine.status_bar_inputs(fleet, {}, 1.0),
        ):
            assert changed != before


def test_socket_path_is_next_to_the_state_file(monkeypatch):
    # pytest's tmp_path on macOS is too long for AF_UNIX (that gets a
    # short stand-in: test_engine_socket.TestLongStateDir)
    import shutil
    import tempfile

    short = tempfile.mkdtemp(prefix="oc-", dir="/tmp")
    try:
        monkeypatch.setenv("OVERCODE_STATE_DIR", short)
        from overcode.settings import get_monitor_daemon_state_path

        assert tui_engine.engine_socket_path("agents") == \
            get_monitor_daemon_state_path("agents").parent / "engine.sock"
    finally:
        shutil.rmtree(short, ignore_errors=True)



# ── DST: 25 Oct 2026 in London, 02:00 BST becomes 01:00 GMT ──────────────


@pytest.fixture
def london(monkeypatch):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _frozen_now(epoch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(epoch, tz)
    return Frozen


class TestAcrossTheClocksGoingBack:
    def test_time_in_state_spanning_the_change_is_the_true_elapsed(self, london):
        """#16: 00:30 BST to 03:00 GMT is 3h30m, not the 2h30m the wall clock says."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from overcode.columns import registry, render_identity
        from overcode.tui_helpers import format_duration

        start = datetime(2026, 10, 25, 0, 30).timestamp()  # BST
        now = datetime(2026, 10, 25, 3, 0).timestamp()      # GMT
        assert now - start == 3.5 * 3600
        changed_at = tui_engine.status_changed_at(
            {"current_status": "waiting_user", "live_colour": "red",
             "episode_colour": "red", "episode_start": start})
        ctx = SimpleNamespace(status_changed_at=changed_at, stats=SimpleNamespace(state_since=None),
                              status_color="red", bg="", status_symbol="x", status="waiting_user")
        with patch.object(render_identity, "datetime", _frozen_now(now)), \
                patch.object(registry, "datetime", _frozen_now(now)):
            cell = render_identity.render_time_in_state(ctx)[0][0]
            plain = render_identity.render_status_plain(ctx)
            age = registry._age_seconds(changed_at)
        assert cell.strip() == format_duration(3.5 * 3600)
        assert f"({format_duration(3.5 * 3600)})" in plain
        assert age == 3.5 * 3600

    def test_a_state_begun_in_the_repeated_hour_is_never_negative(self, london):
        from unittest.mock import patch

        from overcode.columns import registry

        start = datetime(2026, 10, 25, 1, 50).timestamp()  # BST, first pass
        now = start + 20 * 60                                # 01:10 GMT
        changed_at = tui_engine.status_changed_at(
            {"current_status": "running", "live_colour": "green",
             "episode_colour": "green", "episode_start": start})
        with patch.object(registry, "datetime", _frozen_now(now)):
            assert registry._age_seconds(changed_at) == 20 * 60

    def test_a_time_base_in_the_repeated_hour_reads_back_exactly(self, london):
        """#17: the anchor's ISO text dropped fold; the second 01:30 read back as BST."""
        from overcode.tui_helpers import get_current_state_times

        base_at = datetime(2026, 10, 25, 1, 29, 50, fold=1).timestamp()  # GMT, second pass
        assert datetime.fromtimestamp(base_at).fold == 1
        out = tui_engine.session_stats(SessionStats(), {
            "current_status": "running", "time_base": [0.0, 0.0, 0.0],
            "time_base_at": base_at,
        })
        green, _, _ = get_current_state_times(out, now=datetime.fromtimestamp(base_at + 110))
        assert green == pytest.approx(110.0)
