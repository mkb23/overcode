"""The hermes mock's chrome must read the same as the real classic CLI's.

A mock the detector agrees with is the whole point: if these drift apart,
container/E2E runs go green while a real hermes fleet shows the wrong
colour. Each scenario is rendered the way tmux would show it — steps
concatenated, most recent frame at the bottom — and pushed through the same
PollingStatusDetector the daemon uses.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import mock_hermes
from overcode.backends.hermes import HERMES_PATTERNS
from overcode.interfaces import MockTmux
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from overcode.status_detector import PollingStatusDetector
from tests.fixtures import create_mock_session


TMUX_SESSION = "agents"
WINDOW = "hm-mock-1"
MOCK = Path(__file__).parent.parent / "mock_hermes.py"


def render(scenario_name: str, *, stop_after: int = None) -> str:
    scenario = mock_hermes.get_builtin_scenarios()[scenario_name]
    steps = scenario["steps"]
    if stop_after is not None:
        steps = steps[:stop_after]
    return "".join(s["text"] for s in steps if s.get("type") == "output")


def render_label(scenario_name: str, label: str) -> str:
    scenario = mock_hermes.get_builtin_scenarios()[scenario_name]
    body = "".join(s["text"] for s in scenario["steps"] if s.get("type") == "output")
    tail = "".join(s["text"] for s in scenario["labels"][label] if s.get("type") == "output")
    return body + tail


def detect(pane: str):
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=HERMES_PATTERNS
    )
    return detector.detect_status(create_mock_session(name="hm", tmux_window=WINDOW))


class TestScenarios:
    def test_all_three_scenarios_exist(self):
        assert set(mock_hermes.get_builtin_scenarios()) == {
            "hm_launch_and_idle",
            "hm_simple_response",
            "hm_permission_command",
        }

    def test_scenario_names_cannot_collide_with_other_mocks(self):
        import mock_codex
        import mock_grok
        import mock_opencode
        others = set(mock_codex.get_builtin_scenarios()) | set(mock_grok.get_builtin_scenarios()) \
            | set(mock_opencode.get_builtin_scenarios())
        assert not (set(mock_hermes.get_builtin_scenarios()) & others)


class TestDetectorAgreesWithTheMock:
    def test_launch_and_idle_is_waiting_user(self):
        status, _, _ = detect(render("hm_launch_and_idle"))
        assert status == STATUS_WAITING_USER

    def test_completed_turn_is_waiting_user(self):
        status, _, _ = detect(render("hm_simple_response"))
        assert status == STATUS_WAITING_USER

    def test_mid_turn_is_running(self):
        # Banner + user turn + busy bar, before the reply lands.
        status, _, _ = detect(render("hm_simple_response", stop_after=3))
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _ = detect(render("hm_permission_command"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    @pytest.mark.parametrize("label", ["approved", "denied"])
    def test_permission_outcomes_settle_to_waiting_user(self, label):
        status, _, _ = detect(render_label("hm_permission_command", label))
        assert status == STATUS_WAITING_USER


class TestChromeMatchesTheCorpus:
    @pytest.mark.parametrize("marker", ["❯ Draft a reply", " ☤ gpt-5-mini │", "[░░░░░░░░░░]"])
    def test_idle_chrome(self, marker):
        assert marker in mock_hermes.IDLE_PROMPT

    def test_busy_bar_carries_the_interrupt_hint(self):
        assert "☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel" in mock_hermes.BUSY_BAR

    @pytest.mark.parametrize("marker", [
        "⚠️  Dangerous Command", "1. Allow once", "4. Deny", "Enter to confirm", "⚠ ❯",
    ])
    def test_permission_dialog_chrome(self, marker):
        assert marker in mock_hermes.PERMISSION_DIALOG

    def test_interrupted_marker_matches_the_pattern(self):
        assert HERMES_PATTERNS.shows_interrupt_prompt(mock_hermes.INTERRUPTED_MARKER)

    def test_no_claude_codex_or_grok_chrome_leaked_in(self):
        everything = "".join(
            s["text"] for sc in mock_hermes.get_builtin_scenarios().values()
            for s in sc["steps"] if s.get("type") == "output"
        )
        for foreign in ("esc to interrupt", "? for shortcuts", "⏵⏵", "Ask Codex", "Yes, proceed"):
            assert foreign not in everything


class TestNonChatInvocations:
    def test_version_banner(self):
        out = subprocess.run([sys.executable, str(MOCK), "--version"], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0
        assert out.stdout.startswith("Hermes Agent v0.21.3")

    def test_plugins_enable_exits_zero(self):
        out = subprocess.run(
            [sys.executable, str(MOCK), "plugins", "enable", "overcode", "--no-allow-tool-override"],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0
