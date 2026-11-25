"""Budget set/show/transfer/reclaim (coverage matrix row 5), over every mock
backend, and an opencode budget tripped by spend read from its store (#534)."""

import pytest

from tests.container.harness import OPENCODE

pytestmark = pytest.mark.timeout(240)

# An unattended monitor daemon syncs transcript stats once a minute
# (monitor_daemon.STATS_SYNC_UNATTENDED_SECONDS), so a wait for synced spend
# has to outlast one full period.
STATS_SYNC_WAIT = 150


def test_budget_set_and_show(oc, oc_wait, mock_backend):
    oc.launch_on(mock_backend, "spender", scenario="startup_idle")
    oc_wait(lambda: oc.agent("spender"), desc="agent in registry")

    oc.ok("budget", "set", "spender", "5.00")
    assert oc.agent("spender")["cost_budget_usd"] == pytest.approx(5.0)

    result = oc.ok("budget", "show", "spender")
    assert "5" in result.stdout


def test_budget_transfer_debits_parent(oc, oc_wait, mock_backend):
    oc.launch_on(mock_backend, "rich-parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("rich-parent"), desc="parent in registry")
    oc.ok("budget", "set", "rich-parent", "10.00")

    oc.launch_on(mock_backend, "poor-child", "--parent", "rich-parent",
                 scenario="startup_idle")
    oc_wait(lambda: oc.agent("poor-child"), desc="child in registry")

    oc.ok("budget", "transfer", "rich-parent", "poor-child", "4.00")

    assert oc.agent("poor-child")["cost_budget_usd"] == pytest.approx(4.0)
    assert oc.agent("rich-parent")["cost_budget_usd"] == pytest.approx(6.0)


def test_budget_reclaim_refunds_parent(oc, oc_wait, mock_backend):
    oc.launch_on(mock_backend, "reclaimer", scenario="startup_idle")
    oc_wait(lambda: oc.agent("reclaimer"), desc="parent in registry")
    oc.ok("budget", "set", "reclaimer", "10.00")

    oc.launch_on(mock_backend, "temp-child", "--parent", "reclaimer", "--budget", "3.00",
                 scenario="startup_idle")
    oc_wait(lambda: oc.agent("temp-child"), desc="child in registry")
    assert oc.agent("reclaimer")["cost_budget_usd"] == pytest.approx(7.0)

    oc.ok("budget", "reclaim", "temp-child")
    assert oc.agent("reclaimer")["cost_budget_usd"] == pytest.approx(10.0)


def test_launch_budget_without_parent(oc, oc_wait, mock_backend):
    oc.launch_on(mock_backend, "standalone", "--budget", "2.50", scenario="startup_idle")
    agent = oc_wait(lambda: oc.agent("standalone"), desc="agent in registry")
    assert agent["cost_budget_usd"] == pytest.approx(2.5)


def _cost(oc, name):
    return float(((oc.agent(name) or {}).get("stats") or {}).get("estimated_cost_usd") or 0)


def test_opencode_budget_trips_on_stored_cost(oc, oc_wait):
    """One scripted turn costs $0.75 in the mock's store. The daemon must
    take that recorded cost (a gpt-4o-mini token estimate for the same turn
    is a fraction of a cent), and a $0.50 budget then refuses prompts."""
    oc.launch_on(OPENCODE, "spendy", "--budget", "0.50", scenario="one_turn")
    oc.start_monitor_daemon(interval=1)

    oc_wait(lambda: _cost(oc, "spendy") > 0, timeout=STATS_SYNC_WAIT,
            desc="daemon priced the opencode conversation")
    assert _cost(oc, "spendy") == pytest.approx(0.75)
    (row,) = oc.opencode_sessions().values()
    assert oc.agent("spendy")["active_agent_session_id"] == row["id"]

    refused = oc.run("send", "spendy", "keep going")
    assert refused.returncode != 0, refused.stdout
    assert "budget" in (refused.stdout + refused.stderr).lower()
    assert "keep going" not in oc.pane("spendy"), "a refused prompt must not be typed"

    # Raising the budget lets prompts through again
    oc.ok("budget", "set", "spendy", "5.00")
    oc.ok("send", "spendy", "keep going")
    oc_wait(lambda: "keep going" in oc.pane("spendy"), desc="prompt typed after raise")
