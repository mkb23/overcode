"""Running jobs in the TUI (#463): the JOB column and orphans in the monitor bar."""

import pytest

pytestmark = pytest.mark.timeout(240)

TUI_SESSION = "tuijobs"


def test_jobs_column_and_orphans(oc, oc_wait, sandbox, screenshots):
    oc.launch("digger", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: oc.daemon_state().get("sessions"), timeout=60, desc="daemon publishing")

    oc.ok("bash", "sleep 600", "-n", "dig", "--agent", "digger", "--no-follow",
          session_arg=False)
    # No agent: the harness strips OVERCODE_SESSION_NAME, so nothing links it
    oc.ok("bash", "sleep 600", "-n", "stray", "--no-follow", session_arg=False)

    # Without TMUX/TMUX_PANE the TUI counts as watched: nobody is attached
    # here, and an unwatched TUI pauses its timers (jobs refresh included).
    sandbox.new_sized_session(
        TUI_SESSION,
        f"env -u TMUX -u TMUX_PANE python -m overcode.cli monitor --session {oc.session} --sync-target {oc.session}",
        env=oc.env, width=200, height=40,
    )

    def pane():
        return sandbox.capture_pane(TUI_SESSION, "")

    oc_wait(lambda: "digger" in pane(), timeout=90, desc="TUI shows the agent")
    oc_wait(lambda: "1 orphan job" in pane(), timeout=30, desc="orphan job in the monitor bar")

    def agent_row():
        # The agent's row, not its timeline strip
        return next((line for line in pane().splitlines()
                     if "digger" in line and "────" not in line), "")

    def header():
        return next((line for line in pane().splitlines() if "NAME" in line), "")

    # The JOB column is in the full summary detail: step `s` until it shows
    for _ in range(6):
        if " JOB " in header():
            break
        before = header()
        sandbox.send_keys(TUI_SESSION, "", "s")
        oc_wait(lambda: header() != before, timeout=10, desc="summary detail changed")
    oc_wait(lambda: "🚜 1" in agent_row(), timeout=20, desc="JOB column counts 1")

    from overcode.testing.renderer import render_terminal_to_png
    render_terminal_to_png(sandbox.capture_pane_ansi(TUI_SESSION),
                           str(screenshots / "tui-jobs.png"), width=200, height=40)
