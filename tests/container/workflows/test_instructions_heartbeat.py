"""Standing instructions, heartbeat, sleep mode (coverage matrix row 6)."""

import pytest

pytestmark = pytest.mark.timeout(180)


@pytest.fixture
def agent(oc, oc_wait):
    oc.launch("steady", scenario="startup_idle")
    oc_wait(lambda: oc.agent("steady"), desc="agent in registry")
    return "steady"


def test_instruct_sets_and_clears(oc, agent):
    oc.ok("instruct", agent, "keep", "making", "progress")
    assert "keep making progress" in oc.agent(agent)["standing_instructions"]

    oc.ok("instruct", agent, "--clear")
    assert oc.agent(agent)["standing_instructions"] == ""


def test_instruct_preset_resolves(oc, agent):
    presets = oc.ok("instruct", "--list")
    assert "DO_NOTHING" in presets.stdout

    oc.ok("instruct", agent, "DO_NOTHING")
    assert oc.agent(agent)["standing_instructions"] != ""


def test_heartbeat_config_roundtrip(oc, agent):
    oc.ok("heartbeat", agent, "--enable", "--frequency", "5m",
          "--instruction", "carry on")
    data = oc.agent(agent)
    assert data.get("heartbeat_enabled") is True

    show = oc.ok("heartbeat", agent, "--show")
    assert "carry on" in show.stdout or "5m" in show.stdout or "300" in show.stdout

    oc.ok("heartbeat", agent, "--pause")
    assert oc.agent(agent).get("heartbeat_paused") is True

    oc.ok("heartbeat", agent, "--resume")
    assert oc.agent(agent).get("heartbeat_paused") is False

    oc.ok("heartbeat", agent, "--disable")
    assert oc.agent(agent).get("heartbeat_enabled") is False


def test_heartbeat_fires_through_daemon(oc, oc_wait, agent):
    """With the minimum 30s frequency and a 1s monitor interval, the daemon
    should send the heartbeat instruction into the agent's pane."""
    oc.ok("heartbeat", agent, "--enable", "--frequency", "30",
          "--instruction", "HEARTBEAT-PING-42")
    oc.start_monitor_daemon(interval=1)

    oc_wait(
        lambda: "HEARTBEAT-PING-42" in oc.pane(agent),
        timeout=150,
        interval=2,
        desc="heartbeat instruction visible in agent pane",
    )
