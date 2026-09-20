"""The opencode2 mock's chrome must read the same as the real v2 TUI's.

Mirror of ``test_mock_opencode.py`` for the v2 backend: every rendered
frame is grounded in the ``tests/fixtures_opencode2_panes/`` corpus (real
opencode2 v0.0.0-dev-19272 captures), the scenarios replay through the
same PollingStatusDetector the daemon uses (with OPENCODE2_PATTERNS), and
the key sequences verified live against the preview build — bare Enter
approving the preselected "Allow once", Escape rejecting with "The user
declined this tool call", ``/exit`` exiting, ``/new`` resetting to the
banner — are asserted on the output the mock CLI actually renders when
driven as a child process, never on the scenario dict it replays.
"""

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import mock_opencode2
from overcode.backends.opencode2_patterns import OPENCODE2_PATTERNS
from overcode.interfaces import MockTmux
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from overcode.status_detector import PollingStatusDetector
from tests.fixtures import create_mock_session


TMUX_SESSION = "agents"
WINDOW = "oc2-mock-1"


def render(scenario_name: str, *, stop_after: int = None) -> str:
    """Concatenate a scenario's output steps into a pane-shaped string.

    ``stop_after`` truncates mid-scenario, which is how a poll that lands
    while the agent is working sees the pane.
    """
    scenario = mock_opencode2.get_builtin_scenarios()[scenario_name]
    steps = scenario["steps"]
    if stop_after is not None:
        steps = steps[:stop_after]
    return "".join(s["text"] for s in steps if s.get("type") == "output")


def render_label(scenario_name: str, label: str) -> str:
    """Concatenate a scenario label's output steps (a post-menu branch)."""
    scenario = mock_opencode2.get_builtin_scenarios()[scenario_name]
    return "".join(
        s["text"] for s in scenario["labels"][label]
        if s.get("type") == "output"
    )


@functools.lru_cache(maxsize=None)
def run_mock(scenario: str, input_text: str = "hello\n") -> str:
    """Run the mock CLI as a child process; return everything it printed.

    An unmatched input line ends any scenario (the engine's
    unmatched-input fallback), so the full scripted transcript lands in
    stdout. Cached: several chrome tests replay the same run.
    """
    env = dict(os.environ, MOCK_SCENARIO=scenario)
    result = subprocess.run(
        [sys.executable, mock_opencode2.__file__],
        input=input_text, capture_output=True, text=True,
        timeout=30, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def run_mock_tty(scenario: str, interactions, *, timeout: float = 30.0) -> str:
    """Run the mock CLI under a pty, writing each key batch on cue.

    ``interactions`` is a sequence of ``(trigger, keys)`` pairs: after the
    trigger text has rendered (plus a short settle), the keys are written
    to the pty. The settle matters — the engine's menu switches the tty
    to raw mode when it starts waiting, and that switch flushes pending
    input, so keys written before the wait would be lost.

    The pipe-driven ``run_mock`` lands in the menu's non-tty fallback,
    where a bare Enter reads as an empty line and maps to the last
    option. The real TUI confirms the preselected row on Enter —
    behaviour only the tty path reproduces — so the tests that pin
    bare-Enter semantics drive the mock through a pty instead.

    Whatever happens — a read/write error, a failed expectation, a
    timeout — the helper kills and reaps the mock, so a failing test
    never strands a child process.
    """
    import pty
    import select
    import signal
    import time

    pid, fd = pty.fork()
    if pid == 0:  # child: exec the mock with the scenario in its env
        os.environ["MOCK_SCENARIO"] = scenario
        os.execv(sys.executable, [sys.executable, mock_opencode2.__file__])
        os._exit(127)  # pragma: no cover — exec failed

    # Exposed so the leak test can hold the helper to its reap contract.
    run_mock_tty.last_pid = pid

    out = ""
    deadline = time.monotonic() + timeout

    def read_until(trigger: str) -> None:
        nonlocal out
        while trigger not in out and time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise AssertionError(
                    f"mock exited before rendering {trigger!r}; got:\n{out}"
                )
            out += chunk.decode("utf-8", "replace")

    try:
        for trigger, keys in interactions:
            read_until(trigger)
            time.sleep(0.3)  # let the mock reach its input wait
            os.write(fd, keys.encode())
        # Drain: the mock finishes its scripted output and exits.
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # EIO: child exited, slave side closed
                break
            if not chunk:
                break
            out += chunk.decode("utf-8", "replace")
        else:  # pragma: no cover — a wedged mock must not hang the suite
            os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        # Unconditional teardown: every path — success, a read/write
        # error, a failed expectation, the timeout kill — must take the
        # mock down and reap it, or a failing test leaks the child.
        # Killing an exited-but-unreaped child is a no-op that keeps its
        # exit status; once reaped, both calls raise and are swallowed,
        # so the original error propagates unmasked.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass
    return out


def detect(pane: str):
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=OPENCODE2_PATTERNS
    )
    return detector.detect_status(create_mock_session(name="oc2", tmux_window=WINDOW))


def _drive_mock(line: str, scenario: str = "oc2_launch_and_idle") -> subprocess.CompletedProcess:
    """Run the mock as a child process and feed it one input line."""
    env = dict(os.environ, MOCK_SCENARIO=scenario)
    return subprocess.run(
        [sys.executable, mock_opencode2.__file__],
        input=line, capture_output=True, text=True,
        timeout=30, env=env,
    )


class TestScenarios:
    def test_all_four_scenarios_exist(self):
        assert set(mock_opencode2.get_builtin_scenarios()) == {
            "oc2_launch_and_idle",
            "oc2_simple_response",
            "oc2_busy_interrupt",
            "oc2_permission_bash",
        }

    def test_scenario_names_cannot_collide_with_other_mocks(self):
        import mock_claude
        import mock_opencode
        taken = set(mock_claude.get_builtin_scenarios()) | set(
            mock_opencode.get_builtin_scenarios()
        )
        oc2_names = set(mock_opencode2.get_builtin_scenarios())
        assert not (taken & oc2_names)
        assert all(n.startswith("oc2_") for n in oc2_names)


class TestDetectorAgreesWithMockChrome:
    def test_launch_and_idle_is_waiting_user(self):
        status, _activity, _content = detect(render("oc2_launch_and_idle"))
        assert status == STATUS_WAITING_USER

    def test_completed_turn_is_waiting_user(self):
        status, activity, _content = detect(render("oc2_simple_response"))
        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_mid_turn_is_running(self):
        # Steps 0-2: banner, user turn, busy bar — the spinner frame.
        status, _activity, _content = detect(
            render("oc2_simple_response", stop_after=3)
        )
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _content = detect(render("oc2_permission_bash"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    @pytest.mark.parametrize("label", ["allowed", "allowed_always", "rejected"])
    def test_permission_outcomes_settle_to_waiting_user(self, label):
        pane = render("oc2_permission_bash", stop_after=4) + render_label(
            "oc2_permission_bash", label
        )
        status, _activity, _content = detect(pane)
        assert status == STATUS_WAITING_USER


class TestChromeMatchesTheRealCorpus:
    """The mock's rendered OUTPUT carries the captured opencode2 chrome.

    Every frame is asserted on what the mock CLI actually prints when the
    scenario runs (driven as a child process, the way the /exit-handler
    tests below drive it) — never on the module constants the scenarios
    were assembled from.
    """

    @pytest.mark.parametrize("marker", [
        "┃",
        "╹▀",
        "ctrl+p commands",
    ])
    def test_idle_chrome(self, marker):
        # Both idle states the mock renders: the fresh launch (banner +
        # empty input box) and the pane settled after a completed turn.
        assert marker in run_mock("oc2_launch_and_idle")
        assert marker in run_mock("oc2_simple_response")

    def test_fresh_footer_names_the_v2_agents_key(self):
        # idle_fresh.txt: "shift+tab agents  ctrl+p commands" — v2's agents
        # key; v1's was "tab agents". The settled footer drops the hints
        # (the context/cost line replaces them), so only the fresh pane
        # carries it.
        assert "shift+tab agents" in run_mock("oc2_launch_and_idle")

    def test_idle_footer_carries_context_and_cost_inline(self):
        # idle_after_response.txt:
        #   /tmp/opencode/oc2cap-work/plain    10.7K (2%) · $0.01  ctrl+p commands
        assert re.search(r"\d+\.\d+K \(\d+%\) · \$0\.\d+  ctrl\+p commands",
                         run_mock("oc2_simple_response"))

    def test_busy_bar_carries_spinner_and_interrupt_hint(self):
        # busy.txt: a braille frame in the session tab + "esc interrupt"
        # in the bottom bar (the only in-flight signal) — both inside the
        # simple-response transcript the mock prints mid-turn.
        out = run_mock("oc2_simple_response")
        assert "esc interrupt" in out
        assert re.search(r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏", out)

    def test_armed_busy_bar_says_esc_again(self):
        # One Escape into the interrupt sequence the footer hint becomes
        # "esc again to interrupt" (captured live, same session as busy.txt).
        assert "esc again to interrupt" in run_mock("oc2_busy_interrupt")

    @pytest.mark.parametrize("marker", [
        "△ Permission required",
        "Allow once",
        "Always allow",
        "Reject",
        "ctrl+f fullscreen",
        "⇆ select",
        "enter confirm",
    ])
    def test_permission_dialog_chrome(self, marker):
        # "1" answers the dialog's menu (the preselected "Allow once"), so
        # the transcript carries the dialog and the allowed branch.
        assert marker in run_mock("oc2_permission_bash", "1\nhello\n")

    def test_v2_spells_it_always_allow_not_allow_always(self):
        # The load-bearing v1/v2 delta: v2 renamed "Allow always" to
        # "Always allow" — the old spelling must NOT be rendered anywhere.
        blob = "\n".join([
            run_mock("oc2_launch_and_idle"),
            run_mock("oc2_simple_response"),
            run_mock("oc2_busy_interrupt"),
            run_mock("oc2_permission_bash", "1\nhello\n"),
        ])
        assert "Allow always" not in blob
        # v1's agents hint was a bare "tab agents" footer line; v2's is
        # "shift+tab agents". Check v1's whole footer so the shift+tab
        # spelling doesn't trip the substring.
        assert "  tab agents  ctrl+p commands" not in blob

    def test_tool_block_uses_the_real_tool_marker(self):
        # The corpus marker (idle_after_response.txt), asserted as a
        # literal — reading it from OPENCODE2_PATTERNS would let pattern
        # and mock drift together and still pass.
        assert "→" in run_mock("oc2_simple_response")

    def test_no_claude_or_v1_chrome_leaked_in(self):
        # The three pane-only scenarios (the permission run additionally
        # prints the engine's own arrow-key menu, whose "❯" selector is
        # mock-claude engine chrome, not agent chrome).
        blob = "\n".join([
            run_mock("oc2_launch_and_idle"),
            run_mock("oc2_simple_response"),
            run_mock("oc2_busy_interrupt"),
        ])
        for foreign in ("⏺", "? for shortcuts", "esc to interrupt", "❯", "▣", "✱"):
            assert foreign not in blob


class TestVerifiedKeyBehaviours:
    """The live-verified v2 key sequences, asserted on rendered output.

    The mock is driven as a child process (the exit-handler tests'
    pattern) and the assertions are on what it actually prints — the
    menu rows it renders, the branches a bare Enter and /new land in —
    never on the scenario dict the engine replays. The engine maps keys
    to branches exactly the way the real TUI behaved in the Task 2 live
    verification: interactive_menu returns 0 (the preselected "Allow
    once") for a bare Enter and 2 for Escape.
    """

    def test_permission_menu_options_and_goto_map(self):
        # The menu's three v2 options render in order, the first one
        # preselected (the engine's selector marker). An unmatched line
        # answers the menu and ends the run, so the transcript carries
        # the rendered dialog and nothing after it.
        out = run_mock("oc2_permission_bash", "hello\n")
        assert "❯ 1. Allow once" in out
        assert out.index("1. Allow once") < out.index("2. Always allow") < out.index(
            "3. Reject"
        )
        # v2 renamed v1's "Allow always" — the old spelling must not
        # render anywhere.
        assert "Allow always" not in out

    def test_bare_enter_approves_allow_once_and_clears_the_dialog(self):
        # A bare Enter confirms the preselected "Allow once" (the tty
        # menu path — driven under a pty, since the pipe fallback maps an
        # empty line to the last option instead): the command runs
        # ($ echo hi2 … hi2) and the pane settles back at the idle input
        # box — the dialog is gone.
        out = run_mock_tty("oc2_permission_bash", [
            # Once the dialog's menu has rendered, a bare Enter confirms
            # the preselected first option.
            ("3. Reject", "\r"),
            # Once the allowed branch's output landed, any other prompt
            # line just ends the run.
            ("Command exited with code 0.", "hello\r"),
        ])
        assert "$ echo hi2" in out
        assert "hi2" in out
        assert "Command exited with code 0." in out
        # The dialog cleared: the run settles into the idle box (the
        # context+cost footer line) after the command output, and the
        # dialog never renders a second time.
        assert out.count("△ Permission required") == 1
        assert out.index("△ Permission required") < out.index(
            "Command exited with code 0."
        ) < out.rindex("10.7K (2%) · $0.01  ctrl+p commands")

    def test_escape_rejects_with_declined_message(self):
        # Escape → interactive_menu returns 2 → the rejected branch
        # renders "The user declined this tool call" and returns to idle.
        pane = render("oc2_permission_bash", stop_after=4) + render_label(
            "oc2_permission_bash", "rejected"
        )
        assert "The user declined this tool call" in pane
        status, _activity, _content = detect(pane)
        assert status == STATUS_WAITING_USER

    def test_exit_command_exits(self):
        # Verified: /exit + Enter fills the box (autocomplete consumes the
        # Enter) and a trailing bare Enter executes it — the app closes.
        # The mock's readline sees "/exit" as one line, net effect equal.
        # Observed on the child process only: exit code 0 and the
        # handler's EXIT_MESSAGE on stderr (the fallback exit prints
        # nothing).
        result = _drive_mock("/exit\n")
        assert result.returncode == 0
        assert mock_opencode2.EXIT_MESSAGE in result.stderr

    def test_new_command_resets_to_the_banner(self):
        # /new at the prompt resets the pane to the banner + empty input
        # box — the second banner renders after the first idle box. The
        # trailing unmatched line just ends the run.
        out = run_mock("oc2_launch_and_idle", "/new\nhello\n")
        banner_row = "█▀▀█ █▀▀█ █▀▀█ █▀▀▄ █▀▀▀ █▀▀█ █▀▀█ █▀▀█"
        assert out.count(banner_row) == 2  # launch banner + the /new reset
        assert out.count("Ask anything… ") == 2
        # The reset is the SECOND banner: it renders after the first
        # idle box, not as part of the launch sequence.
        assert out.index("Ask anything… ") < out.rindex(banner_row)


class TestGracefulExitSequenceDrivesTheExitHandler:
    """The graceful-exit keys, end to end through the engine.

    overcode's graceful_exit_keys send ``Escape Escape /exit Enter
    Enter`` (src/overcode/backends/opencode2.py). In the mock's cooked
    mode the line discipline keeps both ``\\x1b`` bytes in the readline
    line, so the engine sees ``"\\x1b\\x1b/exit"``. The ``/exit``
    handler must still fire — printing EXIT_MESSAGE before exiting —
    rather than letting the engine fall through to its unmatched-input
    fallback, which also ends the process with code 0 but silently.
    """

    def test_escape_prefixed_exit_line_fires_the_handler(self):
        # The exact line the graceful-exit keys produce in cooked mode
        # (verified with a real pty: readline sees the two ESC bytes).
        result = _drive_mock("\x1b\x1b/exit\n")
        assert result.returncode == 0
        assert mock_opencode2.EXIT_MESSAGE in result.stderr

    def test_plain_exit_line_fires_the_handler(self):
        result = _drive_mock("/exit\n")
        assert result.returncode == 0
        assert mock_opencode2.EXIT_MESSAGE in result.stderr

    def test_unmatched_line_exits_via_the_fallback_not_the_handler(self):
        # The fallback contract: any other line also ends the mock, but
        # without the handler's message — the distinguishing observable.
        result = _drive_mock("hello\n")
        assert result.returncode == 0
        assert mock_opencode2.EXIT_MESSAGE not in result.stderr


class TestTtyHelperNeverLeaksTheMock:
    """run_mock_tty must kill and reap the mock on failure paths too.

    The first line ends the launch scenario (unmatched-input fallback),
    so the second trigger never renders: reading the dead pty raises
    inside read_until — the helper-error path that used to skip the
    reap and strand the child. The finally must have killed and reaped
    it, which ``waitpid`` proves: a reaped pid is no longer a child of
    this process.
    """

    def test_helper_error_still_kills_and_reaps_the_child(self):
        with pytest.raises((OSError, AssertionError)):
            run_mock_tty("oc2_launch_and_idle", [
                ("Ask anything… ", "hello\r"),
                ("a trigger that never renders", "\r"),
            ], timeout=10.0)
        with pytest.raises(ChildProcessError):
            os.waitpid(run_mock_tty.last_pid, os.WNOHANG)
