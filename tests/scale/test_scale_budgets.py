"""Per-tick budgets the fixed code must meet at the ``--quick`` scale.

Each budget is what a site should cost once its scaling cliff is fixed, on
the synthetic fixture from ``scripts/bench_scaling.py`` (50 agents, 2,000
sessions ever launched, 50 MB of live transcripts, 3 h of status history,
30 days of presence, 20k history.jsonl lines). Budgets the CURRENT code
fails are marked ``xfail(strict=True)`` with the audit's R-number, so the
suite is green today and each fix flips its xfail off — a passing xfail
then fails the suite, which is the point: the fixer removes the mark.

Numbers in the ``today:`` comments are from an M-series Mac; budgets carry
at least a 2x margin against them where the brief allowed it, so a faster
box does not turn a strict xfail into an XPASS.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import pytest

import bench_scaling

pytestmark = [pytest.mark.scale, pytest.mark.timeout(900)]

N_AGENTS = bench_scaling.FixtureSpec.quick().agents


def _by_site(results):
    return {r.site: r for r in results}


# One timing pass per site per session — each test reads its row.


@pytest.fixture(scope="session")
def list_sessions_rows(scale_fixture):
    return _by_site(bench_scaling.time_list_sessions(scale_fixture))


@pytest.fixture(scope="session")
def daemon_state_rows(scale_fixture):
    return _by_site(bench_scaling.time_daemon_state_load(scale_fixture))


@pytest.fixture(scope="session")
def window_burn_rows(scale_fixture):
    return _by_site(bench_scaling.time_window_burn(scale_fixture))


@pytest.fixture(scope="session")
def stats_sweep_rows(scale_fixture):
    return _by_site(bench_scaling.time_stats_sweep(scale_fixture))


@pytest.fixture(scope="session")
def status_history_rows(scale_fixture):
    return _by_site(bench_scaling.time_status_history(scale_fixture))


@pytest.fixture(scope="session")
def presence_rows(scale_fixture):
    return _by_site(bench_scaling.time_presence(scale_fixture))


@pytest.fixture(scope="session")
def timeline_rows(scale_fixture):
    return _by_site(bench_scaling.time_timeline_slots(scale_fixture))


@pytest.fixture(scope="session")
def discover_rows(scale_fixture):
    return _by_site(bench_scaling.time_discover_session_ids(scale_fixture))


@pytest.fixture(scope="session")
def capture_rows(scale_fixture):
    return _by_site(bench_scaling.time_capture_selection(scale_fixture))


@pytest.fixture(scope="session")
def daemon_rows(scale_fixture):
    return _by_site(bench_scaling.time_daemon_phases(scale_fixture))


# ── fixture sanity: the harness feeds the production readers real shapes ──


class TestFixtureShape:
    def test_sessions_json_has_every_entry_and_the_live_fleet(self, scale_fixture):
        from overcode.session_manager import SessionManager

        sessions = SessionManager(state_dir=scale_fixture.state_dir).list_sessions()
        assert len(sessions) == bench_scaling.FixtureSpec.quick().sessions
        live = [s for s in sessions if s.tmux_session == scale_fixture.tmux_session]
        assert len(live) == N_AGENTS
        assert all(s.agent_session_ids and s.active_agent_session_id for s in live)
        # ~5 KB per entry, like a real long-lived sessions.json
        per_entry = scale_fixture.sessions_file.stat().st_size / len(sessions)
        assert 4_000 < per_entry < 8_000

    def test_daemon_state_loads_with_the_live_fleet(self, scale_fixture):
        from overcode.monitor_daemon_state import MonitorDaemonState

        state = MonitorDaemonState.load(scale_fixture.daemon_state_path)
        assert state is not None and len(state.sessions) == N_AGENTS
        assert not state.is_stale()

    def test_transcripts_carry_usage_the_reader_counts(self, scale_fixture):
        from overcode.stats_reader import stats_reader_for_session

        session = bench_scaling.live_sessions(scale_fixture)[0]
        stats = stats_reader_for_session(session).get_stats(session)
        assert stats is not None
        assert stats.input_tokens > 0 and stats.output_tokens > 0
        assert stats.cache_read_tokens > 0 and stats.current_context_tokens > 0
        assert stats.interaction_count > 0 and stats.work_times
        assert stats.model == "claude-opus-4-6" and stats.provider == "web"
        assert stats.subagent_count == bench_scaling.FixtureSpec.quick().subagents_per_agent

    def test_status_history_and_presence_parse(self, scale_fixture):
        from overcode.presence_logger import read_presence_history
        from overcode.status_history import StatusHistoryFile

        rows = StatusHistoryFile(scale_fixture.agent_history_path).read(hours=3.0)
        assert len(rows) > N_AGENTS * 1000
        assert {r[1] for r in rows} >= {f"agent-{i:02d}" for i in range(N_AGENTS)}
        assert len(read_presence_history(hours=3.0)) >= 170

    def test_hook_state_drives_detection(self, scale_fixture):
        from overcode.hook_status_detector import HookStatusDetector

        detector = HookStatusDetector(
            scale_fixture.tmux_session, state_dir=scale_fixture.session_dir
        )
        events = {detector._read_hook_state(f"agent-{i:02d}")["event"] for i in range(N_AGENTS)}
        assert events == {"PostToolUse", "Stop"}


# ── TUI 250 ms fast path / 1 s status bar ────────────────────────────────


class TestSharedFileReads:
    def test_list_sessions_warm_is_a_stat(self, list_sessions_rows):
        # was: 73 ms per call at 2,000 entries (11.7 MB), called ~6x/s by the TUI;
        # fixed (R4): one os.stat while sessions.json is unchanged, ~0.01 ms
        assert list_sessions_rows["list_sessions (warm, unchanged file)"].ms_per_call < 2.0

    def test_daemon_state_load_warm_is_a_stat(self, daemon_state_rows):
        # was: 1.1 ms per load at 50 sessions, called ~5x/s by the TUI. The
        # brief's 1 ms budget is within 10% of that number, so the budget is
        # the stat-gated cost instead; fixed (R4): ~0.01 ms per load.
        assert daemon_state_rows["daemon-state load (warm, unchanged file)"].ms_per_call < 0.25


class TestFastPathCaptures:
    def test_captures_per_tick_capped_regardless_of_daemon(self, capture_rows):
        # was: 50 capture-pane/tick (200/s) whenever the daemon looked stale (R6)
        from overcode.tui_logic import NON_FOCUSED_CAPTURES_PER_TICK

        row = capture_rows["capture selection (per tick, any daemon state)"]
        assert row.tmux_cmds <= 1 + NON_FOCUSED_CAPTURES_PER_TICK
        assert row.ms_per_call < 0.5


class TestStatusBarWorker:
    def test_window_burn_warm_is_incremental(self, window_burn_rows):
        # was: 117 ms per pass over 50 MB, once a second (every transcript re-parsed);
        # fixed (R1): one stat per file, appended bytes only, ~4 ms
        assert window_burn_rows["compute_window_burn (warm, no file changed)"].ms_per_call < 20.0

    def test_status_history_warm_read_is_cheap(self, status_history_rows):
        # today: 1.7 ms (a copy of the ~99k rows in the window)
        assert status_history_rows["status-history read (warm, unchanged)"].ms_per_call < 5.0

    def test_status_history_incremental_read_is_cheap(self, status_history_rows):
        # today: 6 ms after one daemon tick's rows are appended
        assert (
            status_history_rows[
                "status-history read (incremental, one daemon tick appended)"
            ].ms_per_call
            < 20.0
        )

    @pytest.mark.xfail(
        strict=True, reason="R10 not fixed yet: mean spin walks every row in the window"
    )
    def test_mean_spin_is_cheap(self, status_history_rows):
        # today: 22 ms over ~99k rows x 48 agents, once a second
        assert status_history_rows["calculate_mean_spin_from_history"].ms_per_call < 10.0


# ── TUI 5 s stats sweep ───────────────────────────────────────────────────


class TestStatsSweep:
    def test_sweep_warm_is_cheap(self, stats_sweep_rows):
        # was: 53 ms per sweep with nothing changed (fresh HistoryFile + O(H) per session);
        # fixed (R3/R8): shared HistoryFile, indexed lookups, incremental transcripts, ~6 ms
        assert stats_sweep_rows["stats sweep get_stats (warm, nothing changed)"].ms_per_tick < 20.0

    def test_sweep_with_one_appended_transcript_is_cheap(self, stats_sweep_rows):
        # was: 55 ms; fixed (R3/R8): the history parse is gated and only the appended
        # bytes of the touched transcript are read, ~6 ms
        assert (
            stats_sweep_rows["stats sweep get_stats (warm, one transcript appended)"].ms_per_tick
            < 25.0
        )


# ── TUI 30 s timeline ─────────────────────────────────────────────────────


class TestTimeline:
    def test_presence_warm_read_is_a_cache_hit(self, presence_rows):
        assert presence_rows["presence read (warm, unchanged)"].ms_per_call < 1.0

    @pytest.mark.xfail(
        strict=True,
        reason="R13 not fixed yet: presence CSV re-parsed whole after every appended row",
    )
    def test_presence_read_after_append_is_incremental(self, presence_rows):
        # today: 29 ms (43k rows re-parsed) every minute the daemon appends a row
        assert presence_rows["presence read (one row appended)"].ms_per_call < 5.0

    @pytest.mark.xfail(
        strict=True,
        reason="R10 not fixed yet: timeline slots bucket every 2 s sample on the main thread",
    )
    def test_timeline_slot_build_is_cheap(self, timeline_rows):
        # today: 57 ms per render for 50 agents over ~270k rows
        assert (
            timeline_rows["timeline build_timeline_slots (render, main thread)"].ms_per_tick < 10.0
        )


# ── daemon: session-id recovery ───────────────────────────────────────────


class TestSessionIdDiscovery:
    def test_discover_warm_is_cheap(self, discover_rows):
        # was: 42 ms per zero-token agent every 10 s at 20k history lines;
        # fixed (R8): memoised per session on history's stat signature, ~0.02 ms
        assert discover_rows["discover_session_ids (warm, history unchanged)"].ms_per_call < 5.0


# ── daemon 2 s tick body ──────────────────────────────────────────────────


class TestDaemonTick:
    SITE = "daemon _detect_and_enrich (steady state)"

    @pytest.mark.xfail(
        strict=True, reason="R5 not fixed yet: sessions.json rewritten twice per agent per tick"
    )
    def test_at_most_one_sessions_json_write_per_tick(self, daemon_rows):
        # today: 100 fsync'd rewrites of an 11.7 MB file per tick
        assert daemon_rows[self.SITE].writes <= 1

    @pytest.mark.xfail(
        strict=True,
        reason="R5 not fixed yet: sessions.json re-read several times per agent per tick",
    )
    def test_at_most_two_sessions_json_reads_per_tick(self, daemon_rows):
        # today: 340 opens per tick (6-8 per agent)
        assert daemon_rows[self.SITE].reads <= 2

    @pytest.mark.xfail(
        strict=True, reason="R5 not fixed yet: tick body is agents x sessions.json size"
    )
    def test_tick_body_fits_the_interval(self, daemon_rows):
        # today: 27.5 s per 2 s tick for 50 agents at 2,000 entries
        assert daemon_rows[self.SITE].ms_per_tick < 500.0

    def test_tick_body_issues_at_most_one_tmux_call_per_agent(self, daemon_rows):
        # today: one capture-pane per agent (R11 wants fewer; this pins no regression)
        assert daemon_rows[self.SITE].tmux_cmds <= N_AGENTS

    def test_publish_state_is_cheap(self, daemon_rows):
        assert daemon_rows["daemon _publish_state"].ms_per_call < 20.0


class TestDaemonPeriodicSyncs:
    @pytest.mark.xfail(
        strict=True, reason="R7 not fixed yet: _sync_process_resources asks tmux for every pane pid"
    )
    def test_process_resources_tmux_calls_bounded(self, daemon_rows):
        # today: 50 get_pane_pid calls (3 tmux commands each on a fresh RealTmux)
        assert daemon_rows["daemon _sync_process_resources"].tmux_cmds <= 2

    def test_process_resources_one_ps_spawn(self, daemon_rows):
        assert daemon_rows["daemon _sync_process_resources"].spawns <= 1

    def test_process_resources_writes_nothing_when_unchanged(self, daemon_rows):
        assert daemon_rows["daemon _sync_process_resources"].writes == 0

    @pytest.mark.xfail(
        strict=True, reason="R7 not fixed yet: _sync_sandbox_state asks tmux for every pane pid"
    )
    def test_sandbox_state_tmux_calls_bounded(self, daemon_rows):
        assert daemon_rows["daemon _sync_sandbox_state"].tmux_cmds <= 2

    def test_sandbox_state_at_most_ps_plus_lsof(self, daemon_rows):
        assert daemon_rows["daemon _sync_sandbox_state"].spawns <= 2

    def test_sandbox_state_writes_nothing_when_unchanged(self, daemon_rows):
        assert daemon_rows["daemon _sync_sandbox_state"].writes == 0
