"""Polling status detection for the opencode2 backend.

Every pane in ``tests/fixtures_opencode2_panes/`` is a verbatim
``tmux capture-pane -p`` of a **real** opencode2 v0.0.0-dev-19272 session,
so these tests are the tripwire for opencode2 TUI chrome drift: when the
preview changes how it draws its input box, spinner, or permission dialog,
the pattern set in ``backends/opencode2_patterns.py`` stops matching and
these fail.

Mirrors ``tests/unit/test_status_detector_opencode.py`` (the v1 twin): the
same ``PollingStatusDetector`` over a ``MockTmux`` pane, fed the captured
lines.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import get_backend
from overcode.backends.opencode import OPENCODE_PATTERNS, OpencodeBackend
from overcode.backends.opencode2 import Opencode2Backend
from overcode.backends.opencode2_patterns import OPENCODE2_PATTERNS
from overcode.interfaces import MockTmux
from overcode.status_constants import (
    STATUS_RUNNING,
    STATUS_TERMINATED,
    STATUS_WAITING_USER,
)
from overcode.status_detector import PollingStatusDetector
from overcode.status_patterns import DEFAULT_PATTERNS, get_patterns
from tests.fixtures import create_mock_session


PANES_DIR = Path(__file__).parent.parent / "fixtures_opencode2_panes"

TMUX_SESSION = "agents"
WINDOW = "oc2-window-1"

# Corpus replay: file -> expected status (brief Step 2, plus exited_shell).
CASES = {
    "idle_fresh.txt": STATUS_WAITING_USER,
    "idle_after_response.txt": STATUS_WAITING_USER,
    "busy.txt": STATUS_RUNNING,
    "permission_required.txt": STATUS_WAITING_USER,
    "command_menu.txt": STATUS_WAITING_USER,
    "error_api_key.txt": STATUS_WAITING_USER,
    "interrupted.txt": STATUS_WAITING_USER,
    "exited_shell.txt": STATUS_TERMINATED,
}


def load_pane(name: str) -> str:
    return (PANES_DIR / f"{name}.txt").read_text(encoding="utf-8")


def detect(pane: str, *, second_pane: str = None):
    """Run the opencode2 polling detector over one (or two) captures.

    Passing ``second_pane`` simulates a second daemon tick with changed
    content, which is what drives the detector's "content changed = active
    work" phase.
    """
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=OPENCODE2_PATTERNS
    )
    session = create_mock_session(name="oc2", tmux_window=WINDOW)
    result = detector.detect_status(session)
    if second_pane is None:
        return result
    tmux.set_pane_content(TMUX_SESSION, WINDOW, second_pane)
    return detector.detect_status(session)


class TestPatternWiring:
    """The registry hands out opencode2's chrome, not Claude's or v1's."""

    def test_backend_registered(self):
        assert get_backend("opencode2").name == Opencode2Backend.name

    def test_patterns_resolve_by_backend_name(self):
        wired = get_patterns("opencode2")
        assert wired.prompt_chars == ["┃"]
        assert "always allow" in wired.permission_patterns
        assert "allow always" not in wired.permission_patterns

    def test_patterns_are_not_claude_patterns(self):
        # Content-based: a pattern set that matches Claude's ❯ prompt is
        # a registry wiring bug, whichever object the registry returns.
        wired = get_patterns("opencode2")
        assert wired.prompt_chars == ["┃"]
        assert "❯" not in wired.prompt_chars
        assert wired.prompt_chars != DEFAULT_PATTERNS.prompt_chars

    def test_patterns_are_not_v1_opencode_patterns(self):
        # v2 renamed the dialog's "Allow always" -> "Always allow"; a v1
        # pattern set that still matches the old spelling is a copy-paste
        # error, not a corpus-derived one.
        assert get_patterns("opencode2") is not OPENCODE_PATTERNS
        assert "allow always" not in OPENCODE2_PATTERNS.permission_patterns
        assert "always allow" in OPENCODE2_PATTERNS.permission_patterns

    def test_v1_patterns_unaffected(self):
        assert get_patterns("opencode") is OPENCODE_PATTERNS
        assert "allow always" in OPENCODE_PATTERNS.permission_patterns


class TestRealisticCorpus:
    """Statuses read off verbatim opencode2 captures."""

    @pytest.mark.parametrize("filename,expected", CASES.items())
    def test_pane_status(self, filename, expected):
        status, _activity, _content = detect(load_pane(filename.removesuffix(".txt")))
        assert status == expected

    def test_fresh_launch_does_not_report_stalled_input(self):
        # The banner screen's "Ask anything… " placeholder sits behind the
        # ┃ gutter and would read as user-typed text to a naive prompt
        # parser. It must not surface as a stall.
        _status, activity, _content = detect(load_pane("idle_fresh"))
        assert "Stalled" not in activity

    def test_settled_after_response_activity(self):
        _status, activity, _content = detect(load_pane("idle_after_response"))
        assert activity == "Waiting for user input"

    def test_mid_generation_is_running_when_spinner_animates(self):
        # The realistic path: the pane changed since the last tick (spinner,
        # token counter), and the detector must not mistake the always-drawn
        # input box for a prompt.
        busy = load_pane("busy")
        status, _activity, _content = detect(busy, second_pane=busy + "\n")
        assert status == STATUS_RUNNING

    def test_settled_response_after_a_turn_beats_content_change(self):
        # A finished turn still churns the pane (token/cost counter), which
        # must not read as "still working".
        idle = load_pane("idle_after_response")
        status, _activity, _content = detect(idle, second_pane=idle + "\n")
        assert status == STATUS_WAITING_USER

    def test_permission_dialog_activity(self):
        _status, activity, _content = detect(load_pane("permission_required"))
        assert activity.startswith("Permission:")

    def test_finished_tool_calls_do_not_read_as_running(self):
        # "→ Skill …" / "+ Thought …" chrome stays on screen after the
        # turn ends — the reason execution_indicators is deliberately
        # empty. idle_after_response.txt carries that finished chrome.
        status, _activity, _content = detect(load_pane("idle_after_response"))
        assert status == STATUS_WAITING_USER

    def test_interrupted_turn_is_waiting_user(self):
        # Double-Escape mid-generation abandons the turn; the pane is back
        # at the input box, so polling must not keep the agent green.
        status, _activity, _content = detect(load_pane("interrupted"))
        assert status == STATUS_WAITING_USER


class TestPatternPredicates:
    """Unit-level checks on the pieces the phases lean on."""

    def test_busy_marker_found_at_pane_bottom(self):
        lines = [
            ln.strip() for ln in load_pane("busy").split("\n") if ln.strip()
        ]
        assert OPENCODE2_PATTERNS.is_busy(lines)

    def test_idle_pane_is_not_busy(self):
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert not OPENCODE2_PATTERNS.is_busy(lines)

    def test_busy_markers_match_the_captured_footer(self):
        # Corpus-proven (busy.txt): the in-flight footer reads
        # "⬝⬝■■■■■■ esc interrupt"; one Escape rewrites it to
        # "esc again to interrupt" (captured live in the same session,
        # one press into the interrupt sequence) — both must be wired.
        footer = (
            load_pane("busy").rstrip().split("\n")[-1].strip().lower()
        )
        assert "esc interrupt" in footer
        assert "esc interrupt" in OPENCODE2_PATTERNS.busy_markers
        assert "esc again to interrupt" in OPENCODE2_PATTERNS.busy_markers

    def test_input_ready_falls_back_to_the_hint_bar(self):
        # The fresh-launch screen centres the box with blank filler under
        # it, so the bare ┃ is out of reach of any bottom-N slice — the
        # hint bar is what proves the TUI is live and accepting input.
        tail = ["⊙ 0 MCP /mcps", "0.0.0-dev-19272"]
        assert not OPENCODE2_PATTERNS.is_input_ready(tail)
        assert OPENCODE2_PATTERNS.is_input_ready(
            tail + ["shift+tab agents  ctrl+p commands"]
        )

    def test_live_pane_shows_input_hint(self):
        assert OPENCODE2_PATTERNS.shows_input_hint(load_pane("idle_fresh"))

    def test_exited_pane_shows_no_input_hint(self):
        assert not OPENCODE2_PATTERNS.shows_input_hint(load_pane("exited_shell"))

    def test_interrupt_marker_matches_the_captured_pane(self):
        # Corpus-proven (interrupted.txt): the finished pill gains
        # "· interrupted", exactly like v1.
        assert OPENCODE2_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("pane", [
        "busy", "idle_fresh", "idle_after_response",
        "permission_required", "command_menu", "error_api_key",
    ])
    def test_interrupt_marker_does_not_fire_on_other_panes(self, pane):
        assert not OPENCODE2_PATTERNS.shows_interrupt_prompt(load_pane(pane))

    @pytest.mark.parametrize("line", [
        "┃ /agents                   Switch agent                                                                           ┃",
        "┃ /exit                     Exit the app                                                                          ┃",
    ])
    def test_slash_menu_rows_match(self, line):
        assert OPENCODE2_PATTERNS.command_menu_re.match(line)

    def test_permission_dialog_rows(self):
        # Corpus-proven (permission_required.txt): v2 renamed v1's
        # "Allow always" to "Always allow".
        dialog = load_pane("permission_required")
        assert "Allow once" in dialog
        assert "Always allow" in dialog
        assert "Allow always" not in dialog
        assert "enter confirm" in dialog

    @pytest.mark.parametrize("field, value", [
        ("background_bash_count_re", "3 bashes"),
        ("subagent_count_re", "2 local agents"),
        ("monitor_count_re", "1 monitor"),
        ("auto_accept_re", "⏵⏵ auto-accept edits on"),
    ])
    def test_claude_only_extractors_never_match(self, field, value):
        # opencode2 has no analogue for these status-bar counters, so their
        # patterns are built to be unmatchable rather than left at Claude's.
        assert getattr(OPENCODE2_PATTERNS, field).search(value) is None

    def test_no_autocomplete_hint_analogue(self):
        assert not OPENCODE2_PATTERNS.is_autocomplete_hint("↵ to send")
