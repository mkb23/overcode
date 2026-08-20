"""The opencode mock's chrome must read the same as the real TUI's.

A mock the detector agrees with is the whole point: if these drift apart,
container/E2E runs go green while a real opencode fleet shows the wrong
colour. Each scenario is rendered the way tmux would show it — steps
concatenated, most recent frame at the bottom — and pushed through the same
PollingStatusDetector the daemon uses.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import mock_opencode
from overcode.backends.opencode import OPENCODE_PATTERNS
from overcode.interfaces import MockTmux
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from overcode.status_detector import PollingStatusDetector
from tests.fixtures import create_mock_session


TMUX_SESSION = "agents"
WINDOW = "oc-mock-1"


def render(scenario_name: str, *, stop_after: int = None) -> str:
    """Concatenate a scenario's output steps into a pane-shaped string.

    ``stop_after`` truncates mid-scenario, which is how a poll that lands
    while the agent is working sees the pane.
    """
    scenario = mock_opencode.get_builtin_scenarios()[scenario_name]
    steps = scenario["steps"]
    if stop_after is not None:
        steps = steps[:stop_after]
    return "".join(s["text"] for s in steps if s.get("type") == "output")


def detect(pane: str):
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=OPENCODE_PATTERNS
    )
    return detector.detect_status(create_mock_session(name="oc", tmux_window=WINDOW))


class TestScenarios:
    def test_all_three_scenarios_exist(self):
        assert set(mock_opencode.get_builtin_scenarios()) == {
            "oc_launch_and_idle",
            "oc_simple_response",
            "oc_permission_bash",
        }

    def test_scenario_names_cannot_collide_with_mock_claude(self):
        import mock_claude
        claude_names = set(mock_claude.get_builtin_scenarios())
        oc_names = set(mock_opencode.get_builtin_scenarios())
        assert not (claude_names & oc_names)
        assert all(n.startswith("oc_") for n in oc_names)


class TestDetectorAgreesWithMockChrome:
    def test_launch_and_idle_is_waiting_user(self):
        status, _activity, _content = detect(render("oc_launch_and_idle"))
        assert status == STATUS_WAITING_USER

    def test_completed_turn_is_waiting_user(self):
        status, activity, _content = detect(render("oc_simple_response"))
        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_mid_turn_is_running(self):
        # Steps 0-2: banner, user turn, busy bar — the spinner frame.
        status, _activity, _content = detect(
            render("oc_simple_response", stop_after=3)
        )
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _content = detect(render("oc_permission_bash"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    @pytest.mark.parametrize("label", ["allowed", "allowed_always", "rejected"])
    def test_permission_outcomes_settle_to_waiting_user(self, label):
        scenario = mock_opencode.get_builtin_scenarios()["oc_permission_bash"]
        pane = render("oc_permission_bash", stop_after=3) + "".join(
            s["text"] for s in scenario["labels"][label]
            if s.get("type") == "output"
        )
        status, _activity, _content = detect(pane)
        assert status == STATUS_WAITING_USER


class TestChromeMatchesTheRealCorpus:
    """The mock's structural markers are the ones captured from opencode."""

    @pytest.mark.parametrize("marker", [
        "┃",
        "╹▀",
        "ctrl+p commands",
    ])
    def test_idle_chrome(self, marker):
        assert marker in mock_opencode.IDLE_PROMPT

    def test_busy_bar_carries_the_interrupt_hint(self):
        assert "esc interrupt" in mock_opencode.BUSY_BAR
        assert OPENCODE_PATTERNS.is_busy(
            [ln.strip() for ln in mock_opencode.BUSY_BAR.split("\n") if ln.strip()]
        )

    @pytest.mark.parametrize("marker", [
        "△ Permission required",
        "Allow once",
        "Allow always",
        "Reject",
        "enter confirm",
    ])
    def test_permission_dialog_chrome(self, marker):
        assert marker in mock_opencode.PERMISSION_DIALOG

    def test_assistant_footer_uses_the_real_block_marker(self):
        assert OPENCODE_PATTERNS.tool_output_marker in mock_opencode.ASSISTANT_FOOTER

    def test_no_claude_chrome_leaked_in(self):
        blob = "".join([
            mock_opencode.BANNER,
            mock_opencode.EMPTY_PROMPT,
            mock_opencode.IDLE_PROMPT,
            mock_opencode.BUSY_BAR,
            mock_opencode.PERMISSION_DIALOG,
        ])
        for claude_only in ("⏺", "? for shortcuts", "esc to interrupt", "❯"):
            assert claude_only not in blob
