"""The codex mock's chrome must read the same as the real TUI's.

A mock the detector agrees with is the whole point: if these drift apart,
container/E2E runs go green while a real codex fleet shows the wrong colour.
Each scenario is rendered the way tmux would show it — steps concatenated,
most recent frame at the bottom — and pushed through the same
PollingStatusDetector the daemon uses.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import mock_codex
from overcode.backends.codex import CODEX_PATTERNS
from overcode.interfaces import MockTmux
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from overcode.status_detector import PollingStatusDetector
from tests.fixtures import create_mock_session


TMUX_SESSION = "agents"
WINDOW = "cx-mock-1"


def render(scenario_name: str, *, stop_after: int = None) -> str:
    """Concatenate a scenario's output steps into a pane-shaped string.

    ``stop_after`` truncates mid-scenario, which is how a poll that lands
    while the agent is working sees the pane.
    """
    scenario = mock_codex.get_builtin_scenarios()[scenario_name]
    steps = scenario["steps"]
    if stop_after is not None:
        steps = steps[:stop_after]
    return "".join(s["text"] for s in steps if s.get("type") == "output")


def detect(pane: str):
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=CODEX_PATTERNS
    )
    return detector.detect_status(create_mock_session(name="cx", tmux_window=WINDOW))


class TestScenarios:
    def test_all_three_scenarios_exist(self):
        assert set(mock_codex.get_builtin_scenarios()) == {
            "cx_launch_and_idle",
            "cx_simple_response",
            "cx_permission_command",
        }

    def test_scenario_names_cannot_collide_with_other_mocks(self):
        import mock_claude
        import mock_opencode
        claude_names = set(mock_claude.get_builtin_scenarios())
        oc_names = set(mock_opencode.get_builtin_scenarios())
        cx_names = set(mock_codex.get_builtin_scenarios())
        assert not (claude_names & cx_names)
        assert not (oc_names & cx_names)
        assert all(n.startswith("cx_") for n in cx_names)


class TestDetectorAgreesWithMockChrome:
    def test_launch_and_idle_is_waiting_user(self):
        status, _activity, _content = detect(render("cx_launch_and_idle"))
        assert status == STATUS_WAITING_USER

    def test_completed_turn_is_waiting_user(self):
        status, activity, _content = detect(render("cx_simple_response"))
        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_mid_turn_is_running(self):
        # Steps 0-2: banner, user turn, busy bar — the spinner frame.
        status, _activity, _content = detect(
            render("cx_simple_response", stop_after=3)
        )
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _content = detect(
            render("cx_permission_command", stop_after=4)
        )
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    @pytest.mark.parametrize("label", ["approved", "approved_always", "rejected"])
    def test_permission_outcomes_settle_to_waiting_user(self, label):
        scenario = mock_codex.get_builtin_scenarios()["cx_permission_command"]
        pane = render("cx_permission_command", stop_after=4) + "".join(
            s["text"] for s in scenario["labels"][label]
            if s.get("type") == "output"
        )
        status, _activity, _content = detect(pane)
        assert status == STATUS_WAITING_USER


class TestChromeMatchesTheRealCorpus:
    """The mock's structural markers are the ones captured from codex."""

    @pytest.mark.parametrize("marker", [
        "Ask Codex to do anything",
        "gpt-5.6-sol high",
    ])
    def test_idle_chrome(self, marker):
        assert marker in mock_codex.IDLE_PROMPT

    def test_busy_bar_carries_the_interrupt_hint(self):
        assert "esc to interrupt" in mock_codex.BUSY_BAR
        assert CODEX_PATTERNS.is_busy(
            [ln.strip() for ln in mock_codex.BUSY_BAR.split("\n") if ln.strip()]
        )

    @pytest.mark.parametrize("marker", [
        "Would you like to run the following command?",
        "Yes, proceed",
        "Press enter to confirm or esc to cancel",
    ])
    def test_permission_dialog_chrome(self, marker):
        assert marker in mock_codex.PERMISSION_DIALOG

    def test_interrupted_marker_matches_the_pattern(self):
        assert CODEX_PATTERNS.shows_interrupt_prompt(mock_codex.INTERRUPTED_MARKER)

    def test_no_claude_or_opencode_chrome_leaked_in(self):
        blob = "".join([
            mock_codex.BANNER,
            mock_codex.IDLE_PROMPT,
            mock_codex.BUSY_BAR,
            mock_codex.PERMISSION_DIALOG,
        ])
        for foreign in ("⏺", "? for shortcuts", "esc to interrupt\n\n  Esc", "┃", "esc interrupt"):
            assert foreign not in blob
