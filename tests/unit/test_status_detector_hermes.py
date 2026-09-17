"""Polling status detection for the hermes backend.

Every pane in ``tests/fixtures_hermes_panes/`` is a verbatim
``tmux capture-pane -p`` of a **real** Hermes Agent v0.21.3 classic-CLI
session, so these tests are the tripwire for chrome drift: when Hermes
changes how it draws its input line, spinner, status bar or approval box,
the pattern set in ``backends/hermes.py`` stops matching and these fail.
Expected statuses come from ``tests/fixtures_hermes_panes/README.md``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import get_backend
from overcode.backends.hermes import (
    HERMES_PATTERNS,
    HermesBackend,
    HermesStatusPatterns,
)
from overcode.interfaces import MockTmux
from overcode.status_constants import (
    STATUS_BUSY_SLEEPING,
    STATUS_RUNNING,
    STATUS_TERMINATED,
    STATUS_WAITING_USER,
)
from overcode.status_detector import PollingStatusDetector
from overcode.status_patterns import DEFAULT_PATTERNS, get_patterns, is_status_bar_line
from tests.fixtures import create_mock_session


PANES_DIR = Path(__file__).parent.parent / "fixtures_hermes_panes"

TMUX_SESSION = "agents"
WINDOW = "hm-window-1"


def load_pane(name: str) -> str:
    return (PANES_DIR / f"{name}.txt").read_text(encoding="utf-8")


def detect(pane: str, *, second_pane: str = None):
    """Run the hermes polling detector over one (or two) captures."""
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=HERMES_PATTERNS
    )
    session = create_mock_session(name="hm", tmux_window=WINDOW)
    result = detector.detect_status(session)
    if second_pane is None:
        return result
    tmux.set_pane_content(TMUX_SESSION, WINDOW, second_pane)
    return detector.detect_status(session)


class TestPatternWiring:
    def test_backend_registered(self):
        assert get_backend("hermes").name == HermesBackend.name

    def test_patterns_resolve_by_backend_name(self):
        assert get_patterns("hermes") is HERMES_PATTERNS

    def test_patterns_are_not_claude_patterns(self):
        assert get_patterns("hermes") is not DEFAULT_PATTERNS
        assert HERMES_PATTERNS.prompt_chars == ["❯"]
        assert isinstance(HERMES_PATTERNS, HermesStatusPatterns)


IDLE_FIXTURES = [
    "idle_fresh",
    "idle_fresh_yolo",
    "idle_after_approval",
    "idle_after_deny",
    "interrupted",
    "interrupted_api",
    "idle_resumed",
]


class TestCorpus:
    @pytest.mark.parametrize("name", IDLE_FIXTURES)
    def test_idle_frames_are_waiting_user(self, name):
        status, activity, _ = detect(load_pane(name))
        assert status == STATUS_WAITING_USER, (name, activity)

    @pytest.mark.parametrize("name", IDLE_FIXTURES)
    def test_idle_frames_stay_waiting_when_status_bar_ticks(self, name):
        # The status bar's timers tick every second; a second poll of the
        # same idle frame with a different tick must not flip to running.
        pane = load_pane(name)
        ticked = pane.replace("⏲ 0s", "⏲ 3s").replace("⏲ 6s", "⏲ 9s").replace("⏲ 8s", "⏲ 11s")
        status, activity, _ = detect(pane, second_pane=ticked)
        assert status == STATUS_WAITING_USER, (name, activity)

    def test_busy_frame_is_running(self):
        status, _, _ = detect(load_pane("busy"))
        assert status == STATUS_RUNNING

    def test_busy_tool_frame_is_running_or_sleeping(self):
        # busy_tool.txt has `sleep 40` in flight; the detector's foreground
        # classification may narrow that to busy_sleeping, which is still
        # the green "agent is working" bucket, not a stall.
        status, _, _ = detect(load_pane("busy_tool"))
        assert status in (STATUS_RUNNING, STATUS_BUSY_SLEEPING)

    @pytest.mark.parametrize("name", ["busy", "busy_tool"])
    def test_busy_frames_stay_running_when_content_changes(self, name):
        pane = load_pane(name)
        status, _, _ = detect(pane, second_pane=pane + "\nmore streamed text\n")
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user_with_permission_detail(self):
        status, activity, _ = detect(load_pane("permission_required"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    def test_permission_dialog_wins_over_its_own_ticking_countdown(self):
        pane = load_pane("permission_required")
        status, activity, _ = detect(pane, second_pane=pane.replace("(298s)", "(297s)"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    def test_new_confirm_box_is_waiting_user(self):
        status, _, _ = detect(load_pane("new_confirm"))
        assert status == STATUS_WAITING_USER

    def test_command_menu_is_waiting_user(self):
        status, _, _ = detect(load_pane("command_menu"))
        assert status == STATUS_WAITING_USER

    def test_exited_shell_is_terminated(self):
        status, _, _ = detect(load_pane("exited_shell"))
        assert status == STATUS_TERMINATED


class TestPredicates:
    @pytest.mark.parametrize("line", [
        "❯ Draft a reply to the last email in my inbox",
        "❯ Summarize what's in this folder",
        "❯",
        "   ❯ typed text   ",
    ])
    def test_ready_prompt_line(self, line):
        assert HermesStatusPatterns.is_ready_prompt_line(line)

    @pytest.mark.parametrize("line", [
        "☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel",
        "⚠ ❯",
        "│ ❯ 1. Allow once                                │",
        "│ ❯ [1] Approve Once — proceed this time only                            │",
    ])
    def test_non_ready_prompt_lines(self, line):
        assert not HermesStatusPatterns.is_ready_prompt_line(line)

    def test_status_bar_and_busy_line_are_filtered_from_the_hash(self):
        assert is_status_bar_line(" ☤ gpt-5-mini │ 12.7K/400K │ [░░░░░░░░░░] 3% │ 12s │ ⏲ 8s", HERMES_PATTERNS)
        assert is_status_bar_line("☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel", HERMES_PATTERNS)
        assert not is_status_bar_line("❯ Draft a reply to the last email in my inbox", HERMES_PATTERNS)

    def test_busy_marker_is_the_swapped_input_line(self):
        lines = load_pane("busy").splitlines()
        assert HERMES_PATTERNS.is_busy([ln.strip() for ln in lines if ln.strip()])
        idle = load_pane("idle_fresh").splitlines()
        assert not HERMES_PATTERNS.is_busy([ln.strip() for ln in idle if ln.strip()])

    def test_input_ready_on_idle_but_not_busy(self):
        idle = [ln.strip() for ln in load_pane("idle_fresh").splitlines() if ln.strip()]
        busy = [ln.strip() for ln in load_pane("busy").splitlines() if ln.strip()]
        assert HERMES_PATTERNS.is_input_ready(idle)
        assert not HERMES_PATTERNS.is_input_ready(busy)

    def test_interrupt_markers(self):
        assert HERMES_PATTERNS.shows_interrupt_prompt(load_pane("interrupted_api"))
        assert not HERMES_PATTERNS.shows_interrupt_prompt(load_pane("idle_fresh"))

    def test_live_frames_carry_the_input_hint_and_scrollback_does_not(self):
        assert HERMES_PATTERNS.shows_input_hint(load_pane("idle_fresh"))
        tail = "\n".join(load_pane("exited_shell").strip().splitlines()[-3:])
        assert not HERMES_PATTERNS.shows_input_hint(tail)
