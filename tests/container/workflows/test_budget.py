"""Budget set/show/transfer/reclaim (coverage matrix row 5)."""

import pytest

pytestmark = pytest.mark.timeout(180)


def test_budget_set_and_show(oc, oc_wait):
    oc.launch("spender", scenario="startup_idle")
    oc_wait(lambda: oc.agent("spender"), desc="agent in registry")

    oc.ok("budget", "set", "spender", "5.00")
    assert oc.agent("spender")["cost_budget_usd"] == pytest.approx(5.0)

    result = oc.ok("budget", "show", "spender")
    assert "5" in result.stdout


def test_budget_transfer_debits_parent(oc, oc_wait):
    oc.launch("rich-parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("rich-parent"), desc="parent in registry")
    oc.ok("budget", "set", "rich-parent", "10.00")

    oc.launch("poor-child", "--parent", "rich-parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("poor-child"), desc="child in registry")

    oc.ok("budget", "transfer", "rich-parent", "poor-child", "4.00")

    assert oc.agent("poor-child")["cost_budget_usd"] == pytest.approx(4.0)
    assert oc.agent("rich-parent")["cost_budget_usd"] == pytest.approx(6.0)


def test_budget_reclaim_refunds_parent(oc, oc_wait):
    oc.launch("reclaimer", scenario="startup_idle")
    oc_wait(lambda: oc.agent("reclaimer"), desc="parent in registry")
    oc.ok("budget", "set", "reclaimer", "10.00")

    oc.launch("temp-child", "--parent", "reclaimer", "--budget", "3.00",
              scenario="startup_idle")
    oc_wait(lambda: oc.agent("temp-child"), desc="child in registry")
    assert oc.agent("reclaimer")["cost_budget_usd"] == pytest.approx(7.0)

    oc.ok("budget", "reclaim", "temp-child")
    assert oc.agent("reclaimer")["cost_budget_usd"] == pytest.approx(10.0)


def test_launch_budget_without_parent(oc, oc_wait):
    oc.launch("standalone", "--budget", "2.50", scenario="startup_idle")
    agent = oc_wait(lambda: oc.agent("standalone"), desc="agent in registry")
    assert agent["cost_budget_usd"] == pytest.approx(2.5)
