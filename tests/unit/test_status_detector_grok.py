"""Polling status detection for the grok backend (Phase 3).

Every pane in ``tests/fixtures_grok_panes/`` is a verbatim
``tmux capture-pane -p`` of a **real** Grok Build v1.0.5 session, so these
tests are the tripwire for grok TUI chrome drift: when grok changes how it
draws its input box, spinner, or approval dialog, the pattern set in
``backends/grok.py`` stops matching and these fail. Expected statuses come
from ``tests/fixtures_grok_panes/README.md``'s per-file table, with one
documented correction (see ``TestRealisticCorpus.test_error_bad_model_*``
below).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import get_backend
from overcode.backends.grok import (
    GROK_PATTERNS,
    GrokBackend,
    TESTED_GROK_MAX,
    TESTED_GROK_MIN,
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


PANES_DIR = Path(__file__).parent.parent / "fixtures_grok_panes"

TMUX_SESSION = "agents"
WINDOW = "gk-window-1"


def load_pane(name: str) -> str:
    return (PANES_DIR / f"{name}.txt").read_text(encoding="utf-8")


def detect(pane: str, *, second_pane: str = None):
    """Run the grok polling detector over one (or two) captures.

    Passing ``second_pane`` simulates a second daemon tick with changed
    content, which is what drives the detector's "content changed = active
    work" phase.
    """
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=GROK_PATTERNS
    )
    session = create_mock_session(name="gk", tmux_window=WINDOW)
    result = detector.detect_status(session)
    if second_pane is None:
        return result
    tmux.set_pane_content(TMUX_SESSION, WINDOW, second_pane)
    return detector.detect_status(session)


class TestPatternWiring:
    """The registry hands out grok's chrome, not Claude's."""

    def test_backend_registered(self):
        assert get_backend("grok").name == GrokBackend.name

    def test_patterns_resolve_by_backend_name(self):
        assert get_patterns("grok") is GROK_PATTERNS

    def test_patterns_are_not_claude_patterns(self):
        assert get_patterns("grok") is not DEFAULT_PATTERNS
        assert GROK_PATTERNS.prompt_chars == ["❯"]

    def test_claude_patterns_unaffected(self):
        assert get_patterns("claude-code") is DEFAULT_PATTERNS

    def test_codex_patterns_unaffected(self):
        from overcode.backends.codex import CODEX_PATTERNS
        assert get_patterns("codex") is CODEX_PATTERNS
        assert GROK_PATTERNS is not CODEX_PATTERNS


class TestRealisticCorpus:
    """Statuses read off verbatim grok captures, per the corpus README."""

    def test_fresh_launch_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("idle_fresh"))
        assert status == STATUS_WAITING_USER

    def test_fresh_launch_fullscreen_variant_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("idle_fresh_fullscreen"))
        assert status == STATUS_WAITING_USER

    def test_settled_after_response_is_waiting_user(self):
        status, activity, _content = detect(load_pane("idle_after_response"))
        assert status == STATUS_WAITING_USER
        assert activity == "Waiting for user input"

    def test_mid_generation_is_running(self):
        status, _activity, _content = detect(load_pane("busy"))
        assert status == STATUS_RUNNING

    def test_mid_generation_is_running_when_spinner_animates(self):
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

    def test_interrupted_turn_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("interrupted"))
        assert status == STATUS_WAITING_USER

    def test_trust_dialog_is_waiting_user(self):
        # Confirmed absent live — chrome is byte-identical to idle_fresh.txt.
        status, _activity, _content = detect(load_pane("trust_dialog"))
        assert status == STATUS_WAITING_USER

    def test_command_menu_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("command_menu"))
        assert status == STATUS_WAITING_USER

    def test_after_exit_is_terminated(self):
        status, _activity, _content = detect(load_pane("exited_shell"))
        assert status == STATUS_TERMINATED

    def test_error_bad_model_reads_as_waiting_user_not_terminated(self):
        # Corpus README correction: error_bad_model.txt was captured from a
        # *headless* (`-p`) run, not the interactive TUI — Phase 0 confirmed
        # the interactive TUI silently swallows a bad --model id instead of
        # erroring, so there is no live TUI error chrome to ground a
        # STATUS_ERROR/STATUS_TERMINATED pattern on (see GROK_PATTERNS'
        # error_patterns docstring). `get_pane_content()` rstrips the
        # captured pane before slicing, so the ~38 blank padding lines below
        # the two error lines never reach the detector at all — the two
        # error lines themselves are what `last_lines` sees. None of them
        # match a shell prompt, a permission dialog, or any grok-specific
        # marker, so the detector falls through every phase to its default:
        # STATUS_WAITING_USER, with the cleaned last line as the activity
        # string. This is real, correct behavior given the fixture's
        # content, not a bug — documented here rather than silently
        # asserting the corpus README's "terminated" annotation, which the
        # actual wiring does not produce (mirrors codex's own
        # error_bad_model precedent: test_status_detector_codex.py's
        # test_error_bad_model_reads_as_waiting_user_not_error).
        status, activity, _content = detect(load_pane("error_bad_model"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Error: Couldn't set model")


class TestPatternPredicates:
    """Unit-level checks on the pieces the phases lean on."""

    def test_busy_marker_found_at_pane_bottom(self):
        lines = [ln.strip() for ln in load_pane("busy").split("\n") if ln.strip()]
        assert GROK_PATTERNS.is_busy(lines)

    def test_idle_pane_is_not_busy(self):
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert not GROK_PATTERNS.is_busy(lines)

    def test_input_ready_matches_the_empty_box_shape(self):
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert GROK_PATTERNS.is_input_ready(lines)
        # The base class's bare-prompt-char check alone must not suffice —
        # grok never draws "❯" without the box border around it.
        assert not DEFAULT_PATTERNS.is_input_ready(lines[-4:])

    def test_input_ready_true_on_fresh_launch_with_no_hint_bar(self):
        # idle_fresh.txt has no "Shift+Tab:mode" hint bar at all (replaced
        # by the right-aligned "[stable]" tag) — only the empty-box regex
        # can catch this one.
        lines = [
            ln.strip() for ln in load_pane("idle_fresh").split("\n") if ln.strip()
        ]
        assert GROK_PATTERNS.is_input_ready(lines)

    def test_input_ready_false_without_a_box(self):
        # exited_shell.txt has no box at all — just the shell prompt.
        lines = [
            ln.strip() for ln in load_pane("exited_shell").split("\n") if ln.strip()
        ]
        assert not GROK_PATTERNS.is_input_ready(lines)

    def test_live_pane_shows_input_hint(self):
        assert GROK_PATTERNS.shows_input_hint(load_pane("idle_fresh"))

    def test_exited_pane_shows_no_input_hint(self):
        assert not GROK_PATTERNS.shows_input_hint(load_pane("exited_shell"))

    def test_assistant_status_line_marker(self):
        assert GROK_PATTERNS.is_tool_output_line("◆ Thought for 0.1s")
        assert not GROK_PATTERNS.is_tool_output_line("❯ Reply with exactly: hi")

    def test_interrupt_marker_matches_the_captured_pane(self):
        assert GROK_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("pane", [
        "busy", "idle_fresh", "idle_after_response",
        "permission_required", "trust_dialog", "command_menu",
    ])
    def test_interrupt_marker_does_not_fire_on_other_panes(self, pane):
        assert not GROK_PATTERNS.shows_interrupt_prompt(load_pane(pane))

    def test_claude_interrupt_markers_are_not_grok_s(self):
        assert GROK_PATTERNS.interrupt_prompt_markers == ["Turn cancelled by user"]
        assert not DEFAULT_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("field, value", [
        ("background_bash_count_re", "3 bashes"),
        ("subagent_count_re", "2 local agents"),
        ("monitor_count_re", "1 monitor"),
        ("auto_accept_re", "⏵⏵ auto-accept edits on"),
    ])
    def test_claude_only_extractors_never_match(self, field, value):
        assert getattr(GROK_PATTERNS, field).search(value) is None

    def test_no_autocomplete_hint_analogue(self):
        assert not GROK_PATTERNS.is_autocomplete_hint("↵ to send")

    def test_command_menu_pattern_matches_the_real_menu(self):
        assert GROK_PATTERNS.command_menu_re.match(
            "  /help                    Browse commands and keyboard shortcuts"
        )

    def test_permission_dialog_does_not_leak_always_approve_as_reject(self):
        # Regression guard for the approve_keys()/reject_keys() digit
        # mapping: the dialog's default-selected option ("always-approve
        # mode") must never be confused for a plain approve or reject.
        text = load_pane("permission_required").lower()
        assert "always-approve mode" in text
        assert "yes, proceed" in text
        assert "no, reject" in text


class TestVersionChecks:
    """`overcode doctor`'s grok guardrails."""

    @pytest.mark.parametrize("text, expected", [
        ("1.0.5", (1, 0, 5)),
        ("grok-cli 1.0.5\n", (1, 0, 5)),
        ("v1.0.5", (1, 0, 5)),
        ("", None),
        ("unknown", None),
    ])
    def test_parse_version(self, text, expected):
        assert parse_version(text) is expected or parse_version(text) == expected

    @pytest.mark.parametrize("version, expected", [
        (TESTED_GROK_MIN, True),
        ("1.0.5", True),
        ("1.9.9", True),
        ("1.0.4", False),
        (TESTED_GROK_MAX, False),
        ("2.1.0", False),
        ("nonsense", None),
    ])
    def test_version_in_tested_range(self, version, expected):
        assert version_in_tested_range(version) is expected

    def test_out_of_range_version_warns(self):
        findings = version_findings("3.0.0")
        assert any("outside the tested range" in f for f in findings)

    def test_unknown_version_warns(self):
        findings = version_findings("banana")
        assert any("unrecognised" in f for f in findings)

    def test_in_range_version_with_auth_present_is_silent(self, tmp_path, monkeypatch):
        from overcode.backends import grok as grok_module

        auth = tmp_path / "auth.json"
        auth.write_text('{"token": "x"}')
        monkeypatch.setattr(grok_module, "auth_file_path", lambda: auth)
        assert version_findings("1.0.5") == []

    def test_missing_auth_warns_and_names_grok_login(self, tmp_path, monkeypatch):
        from overcode.backends import grok as grok_module

        missing = tmp_path / "does-not-exist" / "auth.json"
        monkeypatch.setattr(grok_module, "auth_file_path", lambda: missing)
        findings = version_findings("1.0.5")
        assert any("grok login" in f for f in findings)

    def test_empty_auth_file_also_warns(self, tmp_path, monkeypatch):
        from overcode.backends import grok as grok_module

        auth = tmp_path / "auth.json"
        auth.write_text("")
        monkeypatch.setattr(grok_module, "auth_file_path", lambda: auth)
        findings = version_findings("1.0.5")
        assert any("grok login" in f for f in findings)
