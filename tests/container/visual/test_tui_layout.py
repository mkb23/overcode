"""TUI layout verification via real full-stack runs (design §7.1).

The TUI runs inside a fixed-size tmux pane against real daemon state with
mock agents. Assertions are on captured text layout; ANSI captures are also
rendered to PNG artifacts (tui-eye renderer) for human review.
"""

import pytest

pytestmark = pytest.mark.timeout(240)

TUI_SESSION = "tuiview"


@pytest.fixture
def tui(oc, oc_wait, sandbox):
    """A running `overcode monitor` TUI in a 120x40 pane, agents populated."""
    oc.launch("tui-busy", scenario="task_running")
    oc.launch("tui-idle", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)
    oc_wait(
        lambda: len(oc.daemon_state().get("sessions", [])) >= 2,
        timeout=60,
        desc="daemon publishing both agents",
    )

    sandbox.new_sized_session(
        TUI_SESSION,
        f"python -m overcode.cli monitor --session {oc.session}",
        env=oc.env,
        width=120,
        height=40,
    )

    def tui_pane():
        return sandbox.capture_pane(TUI_SESSION, "")

    oc_wait(
        lambda: "tui-busy" in tui_pane() and "tui-idle" in tui_pane(),
        timeout=90,
        desc="TUI shows both agents",
    )
    return tui_pane


def _snap(sandbox, screenshots, name):
    from overcode.testing.renderer import render_terminal_to_png

    ansi = sandbox.capture_pane_ansi(TUI_SESSION)
    render_terminal_to_png(ansi, str(screenshots / name), width=120, height=40)


def test_tui_lists_agents_with_status(tui, sandbox, screenshots):
    content = tui()
    assert "tui-busy" in content
    assert "tui-idle" in content
    _snap(sandbox, screenshots, "tui-agent-list.png")


def test_tui_fits_narrow_terminal(oc, oc_wait, sandbox, screenshots):
    """An 80x24 terminal must still render without crashing."""
    oc.launch("narrow", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: oc.daemon_state().get("sessions"), timeout=60,
            desc="daemon publishing")

    sandbox.new_sized_session(
        "tuinarrow",
        f"python -m overcode.cli monitor --session {oc.session}",
        env=oc.env,
        width=80,
        height=24,
    )
    oc_wait(
        lambda: "narrow" in sandbox.capture_pane("tuinarrow", ""),
        timeout=90,
        desc="narrow TUI shows the agent",
    )
    from overcode.testing.renderer import render_terminal_to_png
    ansi = sandbox.capture_pane_ansi("tuinarrow")
    render_terminal_to_png(ansi, str(screenshots / "tui-narrow-80x24.png"),
                           width=80, height=24)


def test_tui_help_overlay(tui, sandbox, screenshots, oc_wait):
    sandbox.send_keys(TUI_SESSION, "", "?")
    oc_wait(
        lambda: "help" in sandbox.capture_pane(TUI_SESSION, "").lower()
        or "shortcut" in sandbox.capture_pane(TUI_SESSION, "").lower(),
        timeout=30,
        desc="help overlay appears on ?",
    )
    _snap(sandbox, screenshots, "tui-help-overlay.png")
