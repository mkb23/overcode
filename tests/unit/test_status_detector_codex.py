"""Polling status detection for the codex backend (Phase 1).

Every pane in ``tests/fixtures_codex_panes/`` is a verbatim
``tmux capture-pane -p`` of a **real** Codex CLI v0.150.1 session, so these
tests are the tripwire for codex TUI chrome drift: when codex changes how it
draws its input box, spinner, or approval dialog, the pattern set in
``backends/codex.py`` stops matching and these fail. Expected statuses come
from ``tests/fixtures_codex_panes/README.md``'s per-file table.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import get_backend
from overcode.backends.codex import (
    CODEX_PATTERNS,
    CodexBackend,
    TESTED_CODEX_MAX,
    TESTED_CODEX_MIN,
    parse_version,
    version_findings,
    version_in_tested_range,
)
from overcode.interfaces import MockTmux
from overcode.status_constants import (
    STATUS_RUNNING,
    STATUS_TERMINATED,
    STATUS_WAITING_USER,
)
from overcode.status_detector import PollingStatusDetector
from overcode.status_patterns import DEFAULT_PATTERNS, get_patterns
from tests.fixtures import create_mock_session


PANES_DIR = Path(__file__).parent.parent / "fixtures_codex_panes"

TMUX_SESSION = "agents"
WINDOW = "cx-window-1"


def load_pane(name: str) -> str:
    return (PANES_DIR / f"{name}.txt").read_text(encoding="utf-8")


def detect(pane: str, *, second_pane: str = None):
    """Run the codex polling detector over one (or two) captures.

    Passing ``second_pane`` simulates a second daemon tick with changed
    content, which is what drives the detector's "content changed = active
    work" phase.
    """
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=CODEX_PATTERNS
    )
    session = create_mock_session(name="cx", tmux_window=WINDOW)
    result = detector.detect_status(session)
    if second_pane is None:
        return result
    tmux.set_pane_content(TMUX_SESSION, WINDOW, second_pane)
    return detector.detect_status(session)


class TestPatternWiring:
    """The registry hands out codex's chrome, not Claude's."""

    def test_backend_registered(self):
        assert get_backend("codex").name == CodexBackend.name

    def test_patterns_resolve_by_backend_name(self):
        assert get_patterns("codex") is CODEX_PATTERNS

    def test_patterns_are_not_claude_patterns(self):
        assert get_patterns("codex") is not DEFAULT_PATTERNS
        assert CODEX_PATTERNS.prompt_chars == ["›"]
        assert ">" not in CODEX_PATTERNS.prompt_chars

    def test_claude_patterns_unaffected(self):
        assert get_patterns("claude-code") is DEFAULT_PATTERNS

    def test_opencode_patterns_unaffected(self):
        from overcode.backends.opencode import OPENCODE_PATTERNS
        assert get_patterns("opencode") is OPENCODE_PATTERNS
        assert CODEX_PATTERNS is not OPENCODE_PATTERNS


class TestRealisticCorpus:
    """Statuses read off verbatim codex captures, per the corpus README."""

    def test_fresh_launch_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("idle_fresh"))
        assert status == STATUS_WAITING_USER

    def test_fresh_launch_does_not_report_stalled_input(self):
        # The "Ask Codex to do anything" placeholder must not read as a
        # stalled user prompt with no response.
        _status, activity, _content = detect(load_pane("idle_fresh"))
        assert "Stalled" not in activity

    def test_settled_after_response_is_waiting_user(self):
        status, activity, _content = detect(load_pane("idle_after_response"))
        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_mid_generation_is_running(self):
        status, _activity, _content = detect(load_pane("busy"))
        assert status == STATUS_RUNNING

    def test_mid_generation_is_running_when_spinner_animates(self):
        # The realistic path: the pane changed since the last tick, so the
        # detector must not mistake the always-drawn placeholder for a prompt.
        busy = load_pane("busy")
        status, _activity, _content = detect(busy, second_pane=busy + "\n")
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _content = detect(load_pane("permission_required"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    def test_settled_response_after_a_turn_beats_content_change(self):
        idle = load_pane("idle_after_response")
        status, _activity, _content = detect(idle, second_pane=idle + "\n")
        assert status == STATUS_WAITING_USER

    def test_error_bad_model_reads_as_waiting_user_not_error(self):
        # codex recovers a bad-model turn on its own, settling right back at
        # the ready prompt in the same frame — the corpus README documents
        # waiting_user as the expected status, not error.
        status, _activity, _content = detect(load_pane("error_bad_model"))
        assert status == STATUS_WAITING_USER

    def test_after_exit_is_terminated(self):
        status, _activity, _content = detect(load_pane("exited_shell"))
        assert status == STATUS_TERMINATED

    def test_interrupted_turn_is_waiting_user(self):
        # Escape mid-generation abandons the turn; the pane is back at the
        # input box, so polling must not keep the agent green.
        status, _activity, _content = detect(load_pane("interrupted"))
        assert status == STATUS_WAITING_USER

    def test_trust_dialog_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("trust_dialog"))
        assert status == STATUS_WAITING_USER


class TestPatternPredicates:
    """Unit-level checks on the pieces the phases lean on."""

    def test_busy_marker_found_at_pane_bottom(self):
        lines = [ln.strip() for ln in load_pane("busy").split("\n") if ln.strip()]
        assert CODEX_PATTERNS.is_busy(lines)

    def test_idle_pane_is_not_busy(self):
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert not CODEX_PATTERNS.is_busy(lines)

    def test_input_ready_scans_deeper_than_claude_default(self):
        # Claude's 4-line window misses codex's placeholder text (it's never
        # a bare prompt char); codex's widened window and hint fallback find it.
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert CODEX_PATTERNS.is_input_ready(lines)
        assert not DEFAULT_PATTERNS.is_input_ready(lines[-4:])

    def test_input_ready_false_without_the_placeholder(self):
        # trust_dialog.txt has no "Ask Codex to do anything" anywhere.
        lines = [
            ln.strip() for ln in load_pane("trust_dialog").split("\n") if ln.strip()
        ]
        assert not CODEX_PATTERNS.is_input_ready(lines)

    def test_live_pane_shows_input_hint(self):
        assert CODEX_PATTERNS.shows_input_hint(load_pane("idle_fresh"))

    def test_exited_pane_shows_no_input_hint(self):
        assert not CODEX_PATTERNS.shows_input_hint(load_pane("exited_shell"))

    def test_assistant_status_line_marker(self):
        # is_tool_output_line checks the single tool_output_marker ("•");
        # "■" is still recognised via tool_output_prefixes for display cleanup.
        assert CODEX_PATTERNS.is_tool_output_line("• Working (1s • esc to interrupt)")
        assert not CODEX_PATTERNS.is_tool_output_line("› Ask Codex to do anything")
        assert any(
            "■ Conversation interrupted".startswith(p)
            for p in CODEX_PATTERNS.tool_output_prefixes
        )

    def test_interrupt_marker_matches_the_captured_pane(self):
        assert CODEX_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("pane", [
        "busy", "idle_fresh", "idle_after_response",
        "permission_required", "error_bad_model", "trust_dialog",
    ])
    def test_interrupt_marker_does_not_fire_on_other_panes(self, pane):
        assert not CODEX_PATTERNS.shows_interrupt_prompt(load_pane(pane))

    def test_claude_interrupt_markers_are_not_codex_s(self):
        assert CODEX_PATTERNS.interrupt_prompt_markers == ["Conversation interrupted"]
        assert not DEFAULT_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("field, value", [
        ("background_bash_count_re", "3 bashes"),
        ("subagent_count_re", "2 local agents"),
        ("monitor_count_re", "1 monitor"),
        ("auto_accept_re", "⏵⏵ auto-accept edits on"),
    ])
    def test_claude_only_extractors_never_match(self, field, value):
        assert getattr(CODEX_PATTERNS, field).search(value) is None

    def test_no_autocomplete_hint_analogue(self):
        assert not CODEX_PATTERNS.is_autocomplete_hint("↵ to send")

    def test_no_command_menu_analogue(self):
        assert not CODEX_PATTERNS.command_menu_re.match("  /new    New session  ")


class TestVersionChecks:
    """`overcode doctor`'s codex guardrails."""

    @pytest.mark.parametrize("text, expected", [
        ("0.150.1", (0, 150, 1)),
        ("codex-cli 0.150.1\n", (0, 150, 1)),
        ("v0.148.0", (0, 148, 0)),
        ("", None),
        ("unknown", None),
    ])
    def test_parse_version(self, text, expected):
        assert parse_version(text) is expected or parse_version(text) == expected

    @pytest.mark.parametrize("version, expected", [
        (TESTED_CODEX_MIN, True),
        ("0.150.1", True),
        ("0.999.0", True),
        ("0.147.9", False),
        (TESTED_CODEX_MAX, False),
        ("1.1.0", False),
        ("nonsense", None),
    ])
    def test_version_in_tested_range(self, version, expected):
        assert version_in_tested_range(version) is expected

    def test_in_range_version_still_warns_about_autoupdate(self):
        # No config toggle exists to silence this one — it is always surfaced.
        findings = version_findings("0.150.1")
        assert len(findings) == 1
        assert "in_app_updates" in findings[0]

    def test_out_of_range_version_warns(self):
        findings = version_findings("2.4.0")
        assert any("outside the tested range" in f for f in findings)

    def test_unknown_version_warns(self):
        findings = version_findings("banana")
        assert any("unrecognised" in f for f in findings)
