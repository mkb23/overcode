"""Agent lifecycle: launch -> list/show -> send -> kill -> cleanup.

Coverage matrix row 2 (docs/design/e2e-devcontainer-testing.md §6).
"""

import pytest

pytestmark = pytest.mark.timeout(120)


def test_launch_appears_in_registry_and_tmux(oc, oc_wait, sandbox):
    oc.launch("alpha", scenario="startup_idle")

    agent = oc_wait(lambda: oc.agent("alpha"), desc="agent alpha in registry")
    assert agent["tmux_session"] == oc.session

    assert sandbox.has_session(oc.session)
    windows = sandbox.list_windows(oc.session)
    assert any(w.startswith("alpha") for w in windows), windows

    # The mock claude banner should appear in the pane
    oc_wait(lambda: "Claude" in oc.pane("alpha"), desc="mock claude banner in pane")


def test_list_shows_agent(oc, oc_wait):
    oc.launch("beta", scenario="startup_idle")
    oc_wait(lambda: oc.agent("beta"), desc="agent beta in registry")

    result = oc.ok("list")
    assert "beta" in result.stdout


def test_show_displays_agent_details(oc, oc_wait):
    oc.launch("gamma", scenario="startup_idle")
    oc_wait(lambda: oc.agent("gamma"), desc="agent gamma in registry")

    result = oc.ok("show", "gamma")
    assert "gamma" in result.stdout


def test_send_text_reaches_pane(oc, oc_wait):
    oc.launch("delta", scenario="startup_idle")
    oc_wait(lambda: "Claude" in oc.pane("delta"), desc="mock claude ready")

    oc.ok("send", "delta", "hello from the test")
    oc_wait(
        lambda: "hello from the test" in oc.pane("delta"),
        desc="sent text visible in pane",
    )


def test_kill_removes_agent(oc, oc_wait, sandbox):
    oc.launch("epsilon", scenario="startup_idle")
    oc_wait(lambda: oc.agent("epsilon"), desc="agent epsilon in registry")

    oc.ok("kill", "epsilon")

    oc_wait(
        lambda: not any(
            w.startswith("epsilon") for w in sandbox.list_windows(oc.session)
        ),
        desc="epsilon tmux window gone",
    )
    agent = oc.agent("epsilon")
    assert agent is None or agent.get("status") in ("killed", "done", "archived")


def test_launch_duplicate_name_is_idempotent(oc, oc_wait, sandbox):
    """Re-launching an existing name reuses the window rather than duplicating."""
    oc.launch("zeta", scenario="startup_idle")
    oc_wait(lambda: oc.agent("zeta"), desc="agent zeta in registry")

    result = oc.run(
        "launch", "-n", "zeta", extra_env={"MOCK_SCENARIO": "startup_idle"}
    )
    assert "already exists" in (result.stdout + result.stderr)
    zeta_windows = [
        w for w in sandbox.list_windows(oc.session) if w.startswith("zeta")
    ]
    assert len(zeta_windows) == 1, zeta_windows
