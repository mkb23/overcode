"""The engine's freshness contract (docs/design/engine-0.6.md), as constants.

A "fix" that buys CPU by slowing a loop or widening a cache window has to
change one of these numbers, and then this test, in the same diff: the
#476/#517 lesson. Change the design doc's table with it.
"""

import pytest

pytestmark = pytest.mark.unit


def test_hook_driven_status_reaches_views_within_half_a_second_attended():
    from overcode.engine_wake import WAKE_SCAN_ATTENDED_SECONDS

    assert WAKE_SCAN_ATTENDED_SECONDS <= 0.5


def test_hook_driven_status_is_recorded_within_two_seconds_unattended():
    from overcode.engine_wake import WAKE_SCAN_UNATTENDED_SECONDS

    assert WAKE_SCAN_UNATTENDED_SECONDS <= 2.0


def test_blips_merge_at_g_twenty_seconds():
    from overcode.episodes import EPISODE_MERGE_SECONDS

    assert EPISODE_MERGE_SECONDS == 20.0  # #507: chosen over 10 s and 60 s


def test_views_learn_the_engine_is_alive_every_five_seconds():
    from overcode.engine_socket import PING_SECONDS

    assert PING_SECONDS <= 5.0


def test_burn_window_reuse_stays_inside_its_five_to_thirty_second_contract():
    from overcode.backends.opencode_stats import WINDOW_INDEX_MAX_AGE_SECONDS

    assert 5 <= WINDOW_INDEX_MAX_AGE_SECONDS <= 30


def test_quick_ticks_save_the_state_file_at_least_once_a_second():
    from overcode.engine_wake import QUICK_TICK_STATE_SAVE_SECONDS

    assert QUICK_TICK_STATE_SAVE_SECONDS <= 1.0


def test_stats_columns_refresh_every_five_seconds_while_watched():
    from overcode.monitor_daemon import STATS_SYNC_ATTENDED_SECONDS, STATS_SYNC_UNATTENDED_SECONDS

    assert STATS_SYNC_ATTENDED_SECONDS <= 5
    assert STATS_SYNC_UNATTENDED_SECONDS <= 60  # budgets still enforced unattended


def test_delegated_work_is_reconciled_by_claude_s_list_not_a_timer():
    """agent-observability-0.6.1.md §3.3: once a Stop or SubagentStop has
    carried background_tasks, a quiet background agent stays in flight for
    as long as the list says so; the 1 h timer is only for older state."""
    from overcode import swarm

    assert swarm.LEGACY_STALE_SECONDS == 3600.0
    state = {"subagents": {"a1": {"kind": "subagent", "last_seen": 0.0}},
             "swarm": {"bg_at": 0.0}}
    assert swarm.in_flight(state, 10 * swarm.LEGACY_STALE_SECONDS) == (1, 0)


def test_the_delegated_work_rollup_is_counts_that_hold_still():
    """The 👥 rollup is published every tick (swarm_rollup): counts only, no
    times, so a quiet swarm sends no delta (engine-0.6.md)."""
    from overcode import swarm
    from overcode.monitor_daemon_state import MonitorDaemonState

    state = {"subagents": {"a1": {"kind": "subagent", "last_seen": 0.0}},
             "swarm": {"bg_at": 0.0, "done": {"a2": {"result": "ok", "ended_at": 5.0}}}}
    rollups = [swarm.rollup(state, t) for t in (10.0, 1e4, 1e6)]
    assert rollups[0] == rollups[1] == rollups[2]
    assert all(isinstance(v, int) for v in rollups[0].values())
    assert "swarm_rollup" not in MonitorDaemonState.ENGINE_TICK_AGENT_FIELDS


def test_git_columns_refresh_every_fifteen_seconds_and_burn_every_ten():
    from overcode.monitor_daemon import BURN_SYNC_SECONDS, GIT_SYNC_SECONDS

    assert GIT_SYNC_SECONDS <= 15
    assert BURN_SYNC_SECONDS <= 10


# agent-observability-0.6.1.md §6.3: phase 2's virtual rows


def test_virtual_rows_are_checked_at_every_wake_step():
    """An expanded parent's rows (a new subscription, a finished row's time
    up, a moved workflow journal) are checked at every wake step: ≤ 0.5 s
    attended, ≤ 1 s unattended (the step), so workflow progress meets its
    ≤ 1 s / ≤ 2 s contract. Hook-driven row changes ride the quick tick."""
    import inspect

    from overcode.engine_wake import WAKE_SCAN_ATTENDED_SECONDS, WakeLoop

    assert WAKE_SCAN_ATTENDED_SECONDS <= 0.5
    source = inspect.getsource(WakeLoop.sleep)
    assert source.index("publish_swarm_if_due") < source.index("WAKE_SCAN_UNATTENDED_SECONDS:")


def test_finished_virtual_rows_stay_thirty_seconds_by_default():
    from overcode.config import FINISHED_ROW_SECONDS_DEFAULT

    assert FINISHED_ROW_SECONDS_DEFAULT == 30.0  # owner decision 3


def test_virtual_rows_hold_still_between_events():
    """Rows hold absolute times and counts (no countdowns, no last_seen), so
    a quiet swarm sends no delta however often it is rebuilt."""
    from overcode import swarm

    state = {"subagents": {"a1": {"kind": "subagent", "started_at": 1.0, "last_seen": 2.0}},
             "swarm": {"bg_at": 0.0, "done": {"a2": {"result": "ok", "ended_at": 5.0}}}}
    frames = [swarm.virtual_rows("p", state, t, 30.0)[0] for t in (10.0, 20.0, 34.9)]
    assert frames[0] == frames[1] == frames[2]


def test_virtual_row_spend_moves_at_the_stats_cadence():
    """A row's cost comes from the parent's per-conversation spend, which the
    stats sync refreshes at 5 s watched / 60 s unwatched; nothing faster."""
    from overcode.monitor_daemon import STATS_SYNC_ATTENDED_SECONDS, STATS_SYNC_UNATTENDED_SECONDS

    assert STATS_SYNC_ATTENDED_SECONDS <= 5 and STATS_SYNC_UNATTENDED_SECONDS <= 60


# agent-observability-0.6.1.md §5: phase 4's swarm timeline


def test_swarm_history_is_written_on_change_not_per_tick(tmp_path):
    """A parent's counts go to swarm_history.csv when a hook event moves
    them; a tick that saw nothing new counts nothing and writes nothing.
    While something is live a keepalive goes out every 5 min, and a live
    row stands at most two keepalives (a stopped daemon is not live)."""
    from overcode import swarm, swarm_history

    assert swarm_history.KEEPALIVE_SECONDS == 300.0
    assert swarm_history.STALE_AFTER_SECONDS == 2 * swarm_history.KEEPALIVE_SECONDS
    writer = swarm_history.SwarmHistoryWriter(tmp_path / "swarm_history.csv")
    state = {"event": "PreToolUse", "timestamp": 1.0,
             "subagents": {"a1": {"kind": "subagent"}}, "swarm": {"bg_at": 0.0}}
    rollup = swarm.rollup(state, 1.0)
    assert writer.record("p", "p", state, rollup, 1.0) == 2
    for t in range(2, 300):
        assert writer.record("p", "p", state, rollup, float(t)) == 0
    assert writer.computes == 1


def test_the_timeline_strips_refresh_with_the_timeline():
    """The density rows are read by the timeline's own 30 s worker (one
    incremental read of one file); folding them with → / ← reads nothing."""
    from overcode.tui_timers import TIMER_INTERVALS

    assert TIMER_INTERVALS["timeline"] == 30


def test_density_height_is_a_mean_never_a_median():
    from datetime import datetime, timedelta

    from overcode import swarm_history as sh

    now = datetime(2026, 10, 17, 12, 0, 0)
    pts = [sh.Point(now - timedelta(minutes=60), (9, 0, 0, 0, 0, 0, 0), "", ""),
           sh.Point(now - timedelta(minutes=58), (0, 0, 0, 0, 0, 0, 0), "", ""),
           sh.Point(now - timedelta(minutes=57), (0, 0, 0, 0, 0, 0, 0), "", "")]
    slot = sh.density(pts, 20, 1.0, now)[0]  # 3 min: 9 for 2 min, 0 for 1
    assert slot.mean == 6.0  # the median of the samples would be 0


def test_background_sessions_reach_views_within_two_seconds_watched():
    """agent-observability-0.6.1.md, phase 3: a background session's state
    reaches views within 2 s watched, 10 s not (a scan is a stat per job)."""
    from overcode.claude_bg import SCAN_ATTENDED_SECONDS, SCAN_UNATTENDED_SECONDS

    assert SCAN_ATTENDED_SECONDS <= 1.0 and SCAN_UNATTENDED_SECONDS <= 5.0


def test_background_session_spend_moves_at_the_stats_cadence():
    """A 🌙 row's spend is read from its transcript at the stats cadence while
    it runs (tests/unit/test_claude_bg_ledger.py: TestLive); its ledger rows
    follow ledger.checkpoint_minutes, then a final row when it ends."""
    import inspect

    from overcode.monitor_daemon import MonitorDaemon

    source = inspect.getsource(MonitorDaemon._refresh_claude_bg)
    assert "STATS_SYNC_ATTENDED_SECONDS" in source and "STATS_SYNC_UNATTENDED_SECONDS" in source


@pytest.mark.parametrize("hooks", ["current", "older"])
def test_a_launched_subagent_reaches_the_rollup_within_one_wake_scan(hooks, tmp_path):
    """A subagent counts under 👥 from the first hook event that names it
    (its Agent call's async_launched response, or its SubagentStart), so it
    reaches views within one wake scan of its launch, like any other
    hook-driven state. "older": a CLI launched before overcode registered
    SubagentStart/SubagentStop, whose agents used to wait for the parent's
    next Stop (a busy parent: about a minute)."""
    from overcode.engine_wake import WAKE_SCAN_ATTENDED_SECONDS
    from tests.status_replay import REGISTERED_EVENTS, Scenario, capture_steps, replay

    name = "12-background-subagent-own-shell"
    steps = capture_steps(name)
    named = min(s.t for s in steps if s.payload.get("hook_event_name") == "SubagentStart"
                or (s.payload.get("hook_event_name") == "PostToolUse"
                    and s.payload.get("tool_name") == "Agent"))
    registered = None if hooks == "current" else (
        REGISTERED_EVENTS - {"SubagentStart", "SubagentStop", "TeammateIdle"})
    scenario = Scenario(name, steps=steps, expect=[(0.0, named + 3.0, "green")],
                        registered=registered)
    samples = replay(scenario, tmp_path, cadence=WAKE_SCAN_ATTENDED_SECONDS)
    first = next(s.t for s in samples if s.rollup and s.rollup.get("running"))
    assert first - named <= WAKE_SCAN_ATTENDED_SECONDS
