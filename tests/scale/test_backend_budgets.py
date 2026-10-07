"""Per-second budgets for the non-Claude stats readers at the TUI's cadence.

``test_scale_budgets.py`` runs on a Claude-only fleet, so no budget covered
the opencode, opencode2, codex, grok or hermes readers. That gap is how the
opencode burn-rate rescan reached a user at 37% CPU (#517). These tests build
one synthetic store per backend with ``scripts/bench_backends.py`` (30 agents,
3 conversations each where the backend has them, opencode conversations that
fill the 500-row scan, 4 MB logs) and drive each
reader as the TUI does: stats per agent every 5 s, window usage per agent
every second.

Timing budgets are fleet milliseconds of reader work per simulated second;
50 ms/s is 5% of a core. Where the fixed cost is close to today's, the test
counts the wasted work instead (row bodies re-fetched, config re-parsed), which
is deterministic. Budgets the current code fails are ``xfail(strict=True)``
naming their issue, so a fix that lands flips the test and the mark comes off.

Numbers in ``today:`` comments are from an M-series Mac.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import os
from unittest.mock import patch

import pytest

import bench_backends

pytestmark = [pytest.mark.scale, pytest.mark.timeout(900)]

FLEET_BUDGET_MS_PER_S = 50.0


def _known(issue, today):
    return pytest.mark.xfail(strict=True, reason=f"#{issue}: today {today}")


@pytest.fixture(scope="session")
def fleets(tmp_path_factory):
    spec = bench_backends.BackendFleetSpec.quick()
    root = tmp_path_factory.mktemp("overcode-backends")
    built = bench_backends.build_fleets(root, spec, quiet=True)
    # The hermes reader parses $HERMES_HOME/config.yaml; never the host's.
    with patch.dict(os.environ, {"HERMES_HOME": str(bench_backends.hermes_home_for(built))}):
        yield built


@pytest.fixture(scope="session")
def cadence(fleets):
    """One timing pass per fleet, each from cold module caches."""
    results = {}
    for name, fleet in fleets.items():
        bench_backends.clear_reader_caches()
        results[name] = bench_backends.time_cadence(fleet)
    return results


# ── fixture sanity: every reader finds real numbers on its store ──────────


@pytest.mark.parametrize("name", ["opencode", "opencode2", "opencode-cliff",
                                  "codex", "grok", "hermes"])
def test_every_agent_reads_real_stats(fleets, name):
    fleet = fleets[name]
    assert len(fleet.sessions) >= 30
    for session in fleet.sessions:
        stats = fleet.reader.get_stats(session)
        assert stats is not None, session.name
        assert stats.input_tokens > 0 and stats.interaction_count > 0, session.name


@pytest.mark.parametrize("name", ["opencode", "opencode2", "codex", "grok"])
def test_window_reads_real_usage(fleets, name):
    """Budgets must time a window that sums tokens, not an early return.

    The opencode stores span the last day, so their window reaches back
    further than the baseline the cadence uses; the scan is the same.
    """
    from datetime import datetime, timedelta

    fleet = fleets[name]
    since = datetime.now() - (timedelta(days=2) if name.startswith("opencode")
                              else bench_backends.BASELINE)
    usage = fleet.reader.get_window_token_usage(fleet.sessions[0], since)
    assert usage["input_tokens"] > 0


# The global row cache's cap before #526; the cliff fleet must poll past it.
OLD_ROW_CACHE_MAX = 50_000


def test_cliff_fleet_polls_past_the_old_row_cache_cap(fleets):
    fleet = fleets["opencode-cliff"]
    polled = len(fleet.sessions) * bench_backends.BackendFleetSpec.quick().conversations * 500
    assert polled > OLD_ROW_CACHE_MAX


# ── budgets ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["opencode", "opencode2"])
def test_opencode_fleet_within_budget(cadence, name):
    # today: ~25-29 ms/s. Before #517: ~95-116 ms/s (window rescanned every second)
    assert cadence[name].ms_per_second < FLEET_BUDGET_MS_PER_S, cadence[name].line()


@pytest.mark.parametrize("name", ["opencode", "opencode2"])
def test_opencode_burn_rate_reuses_the_stats_scan(cadence, name):
    """The #517 guard: the per-second window costs a lookup, not a scan.

    today: ~9 ms/s. Before #517: ~79-97 ms/s.
    """
    assert cadence[name].window_ms_per_second < 25.0, cadence[name].line()


def test_codex_fleet_within_budget(cadence):
    # today: ~5 ms/s, nearly all of it locating the rollout file; the fold
    # itself is one stat per call. Before #524: ~407 ms/s (the whole rollout
    # re-read every 5 s and every second)
    assert cadence["codex"].ms_per_second < FLEET_BUDGET_MS_PER_S, cadence["codex"].line()


def test_grok_fleet_within_budget(cadence):
    # today: ~1 ms/s. Before #525: ~412 ms/s (the whole updates.jsonl
    # re-read every 5 s and every second)
    assert cadence["grok"].ms_per_second < FLEET_BUDGET_MS_PER_S, cadence["grok"].line()


def test_hermes_fleet_within_budget(cadence):
    # today: ~21-26 ms/s (~33 before #527); 80 leaves room for slower boxes
    assert cadence["hermes"].ms_per_second < 80.0, cadence["hermes"].line()


def test_hermes_warm_sweep_parses_no_config(fleets):
    import yaml

    fleet = fleets["hermes"]
    for session in fleet.sessions:
        fleet.reader.get_stats(session)  # warm
    real = yaml.safe_load
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    with patch.object(yaml, "safe_load", counting):
        for session in fleet.sessions:
            fleet.reader.get_stats(session)
    assert calls == []


def test_opencode_row_cache_holds_the_fleet(fleets, monkeypatch):
    """A warm sweep over a fleet that polls 75k rows re-fetches no bodies.

    Before #526 the 50k-row global cap re-fetched all 75,000 every sweep.
    """
    from overcode.backends import opencode_stats

    fleet = fleets["opencode-cliff"]
    bench_backends.clear_reader_caches()
    for _ in range(2):  # two sweeps: anything a fitting cache keeps is warm
        for session in fleet.sessions:
            fleet.reader.get_stats(session)
    fetched = []
    real = opencode_stats._fetch_data

    def counting(conn, table, ids):
        fetched.extend(ids)
        return real(conn, table, ids)

    monkeypatch.setattr(opencode_stats, "_fetch_data", counting)
    for session in fleet.sessions:
        fleet.reader.get_stats(session)
    assert len(fetched) == 0
