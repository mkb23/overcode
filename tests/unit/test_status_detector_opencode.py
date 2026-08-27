"""Polling status detection for the opencode backend (Phase 4).

Every pane in ``tests/fixtures_opencode_panes/`` is a verbatim
``tmux capture-pane -p`` of a **real** opencode v1.18.19 session, so these
tests are the tripwire for opencode TUI chrome drift: when opencode changes
how it draws its input box, spinner, or permission dialog, the pattern set
in ``backends/opencode.py`` stops matching and these fail.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import get_backend
from overcode.backends.opencode import (
    OPENCODE_PATTERNS,
    OpencodeBackend,
    TESTED_OPENCODE_MAX,
    TESTED_OPENCODE_MIN,
    autoupdate_enabled,
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


PANES_DIR = Path(__file__).parent.parent / "fixtures_opencode_panes"

TMUX_SESSION = "agents"
WINDOW = "oc-window-1"


def load_pane(name: str) -> str:
    return (PANES_DIR / f"{name}.txt").read_text(encoding="utf-8")


def detect(pane: str, *, second_pane: str = None):
    """Run the opencode polling detector over one (or two) captures.

    Passing ``second_pane`` simulates a second daemon tick with changed
    content, which is what drives the detector's "content changed = active
    work" phase.
    """
    tmux = MockTmux()
    tmux.set_pane_content(TMUX_SESSION, WINDOW, pane)
    detector = PollingStatusDetector(
        TMUX_SESSION, tmux=tmux, patterns=OPENCODE_PATTERNS
    )
    session = create_mock_session(name="oc", tmux_window=WINDOW)
    result = detector.detect_status(session)
    if second_pane is None:
        return result
    tmux.set_pane_content(TMUX_SESSION, WINDOW, second_pane)
    return detector.detect_status(session)


class TestPatternWiring:
    """The registry hands out opencode's chrome, not Claude's."""

    def test_backend_registered(self):
        assert get_backend("opencode").name == OpencodeBackend.name

    def test_patterns_resolve_by_backend_name(self):
        assert get_patterns("opencode") is OPENCODE_PATTERNS

    def test_patterns_are_not_claude_patterns(self):
        assert get_patterns("opencode") is not DEFAULT_PATTERNS
        assert OPENCODE_PATTERNS.prompt_chars == ["┃"]
        assert "❯" not in OPENCODE_PATTERNS.prompt_chars

    def test_claude_patterns_unaffected(self):
        assert get_patterns("claude-code") is DEFAULT_PATTERNS


class TestRealisticCorpus:
    """Statuses read off verbatim opencode captures."""

    def test_fresh_launch_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("idle_fresh"))
        assert status == STATUS_WAITING_USER

    def test_fresh_launch_does_not_report_stalled_input(self):
        # The banner screen's "Ask anything… " placeholder sits behind the
        # ┃ gutter and would read as user-typed text to a naive prompt
        # parser. It must not surface as a stall.
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
        # detector must not mistake the always-drawn input box for a prompt.
        busy = load_pane("busy")
        status, _activity, _content = detect(busy, second_pane=busy + "\n")
        assert status == STATUS_RUNNING

    def test_permission_dialog_is_waiting_user(self):
        status, activity, _content = detect(load_pane("permission_required"))
        assert status == STATUS_WAITING_USER
        assert activity.startswith("Permission:")

    def test_settled_response_after_a_turn_beats_content_change(self):
        # A finished turn still churns the pane (token counter, elapsed
        # time), which must not read as "still working".
        idle = load_pane("idle_after_response")
        status, _activity, _content = detect(idle, second_pane=idle + "\n")
        assert status == STATUS_WAITING_USER

    def test_command_menu_is_waiting_user(self):
        status, _activity, _content = detect(load_pane("command_menu"))
        assert status == STATUS_WAITING_USER

    def test_finished_tool_calls_do_not_read_as_running(self):
        # "→ Read README.md" / "✱ Glob …" stay on screen after the turn
        # ends — the reason execution_indicators is deliberately empty.
        status, _activity, _content = detect(load_pane("tool_execution"))
        assert status == STATUS_WAITING_USER

    def test_provider_error_does_not_read_as_running(self):
        status, _activity, _content = detect(load_pane("error_api_key"))
        assert status == STATUS_WAITING_USER

    def test_after_exit_is_terminated(self):
        status, _activity, _content = detect(load_pane("exited_shell"))
        assert status == STATUS_TERMINATED

    def test_interrupted_turn_is_waiting_user(self):
        # Double-Escape mid-generation abandons the turn; the pane is back at
        # the input box, so polling must not keep the agent green.
        status, _activity, _content = detect(load_pane("interrupted"))
        assert status == STATUS_WAITING_USER


class TestPatternPredicates:
    """Unit-level checks on the pieces the phases lean on."""

    def test_busy_marker_found_at_pane_bottom(self):
        lines = [ln.strip() for ln in load_pane("busy").split("\n") if ln.strip()]
        assert OPENCODE_PATTERNS.is_busy(lines)

    def test_idle_pane_is_not_busy(self):
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert not OPENCODE_PATTERNS.is_busy(lines)

    def test_input_ready_scans_deeper_than_claude_default(self):
        # Claude's 4-line window misses opencode's bare ┃; opencode's
        # widened window finds it.
        lines = [
            ln.strip()
            for ln in load_pane("idle_after_response").split("\n")
            if ln.strip()
        ]
        assert OPENCODE_PATTERNS.is_input_ready(lines[-8:])
        assert not DEFAULT_PATTERNS.is_input_ready(lines[-8:])

    def test_input_ready_falls_back_to_the_hint_bar(self):
        # The fresh-launch screen centres the box with blank filler under
        # it, so the bare ┃ is out of reach of any bottom-N slice — the
        # hint bar is what proves the TUI is live and accepting input.
        tail = ["● Tip Create a plugin", "~/code/demo-proj:main    1.18.19"]
        assert not OPENCODE_PATTERNS.is_input_ready(tail)
        assert OPENCODE_PATTERNS.is_input_ready(
            tail + ["tab agents  ctrl+p commands"]
        )

    def test_live_pane_shows_input_hint(self):
        assert OPENCODE_PATTERNS.shows_input_hint(load_pane("idle_fresh"))

    def test_exited_pane_shows_no_input_hint(self):
        assert not OPENCODE_PATTERNS.shows_input_hint(load_pane("exited_shell"))

    def test_assistant_block_marker(self):
        assert OPENCODE_PATTERNS.is_tool_output_line("▣  Build · GPT-4o mini · 6.0s")
        assert not OPENCODE_PATTERNS.is_tool_output_line("⏺ Read(src/main.py)")

    @pytest.mark.parametrize("line", [
        "┃ /exit         Exit the app                            ┃",
        "┃ /new          New session                             ┃",
    ])
    def test_slash_menu_rows_match(self, line):
        assert OPENCODE_PATTERNS.command_menu_re.match(line)

    @pytest.mark.parametrize("field, value", [
        ("background_bash_count_re", "3 bashes"),
        ("subagent_count_re", "2 local agents"),
        ("monitor_count_re", "1 monitor"),
        ("auto_accept_re", "⏵⏵ auto-accept edits on"),
    ])
    def test_claude_only_extractors_never_match(self, field, value):
        # opencode has no analogue for these status-bar counters, so their
        # patterns are built to be unmatchable rather than left at Claude's.
        assert getattr(OPENCODE_PATTERNS, field).search(value) is None

    def test_no_autocomplete_hint_analogue(self):
        assert not OPENCODE_PATTERNS.is_autocomplete_hint("↵ to send")

    def test_interrupt_marker_matches_the_captured_pane(self):
        # Feeds the hook detector's "stuck RUNNING but the user interrupted"
        # downgrade — captured live in Phase 6, not guessed.
        assert OPENCODE_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))

    @pytest.mark.parametrize("pane", [
        "busy", "idle_fresh", "idle_after_response", "tool_execution",
        "permission_required", "command_menu", "error_api_key",
    ])
    def test_interrupt_marker_does_not_fire_on_other_panes(self, pane):
        # "esc again to interrupt" is the busy hint, not an interrupt report.
        assert not OPENCODE_PATTERNS.shows_interrupt_prompt(load_pane(pane))

    def test_claude_interrupt_markers_are_not_opencode_s(self):
        assert OPENCODE_PATTERNS.interrupt_prompt_markers == ["· interrupted"]
        assert not DEFAULT_PATTERNS.shows_interrupt_prompt(load_pane("interrupted"))


class TestVersionChecks:
    """`overcode doctor`'s opencode guardrails."""

    @pytest.mark.parametrize("text, expected", [
        ("1.18.19", (1, 18, 19)),
        ("opencode 1.18.19\n", (1, 18, 19)),
        ("v2.0.0", (2, 0, 0)),
        ("", None),
        ("unknown", None),
    ])
    def test_parse_version(self, text, expected):
        assert parse_version(text) is expected or parse_version(text) == expected

    @pytest.mark.parametrize("version, expected", [
        (TESTED_OPENCODE_MIN, True),
        ("1.18.19", True),
        ("1.99.0", True),
        ("1.17.9", False),
        (TESTED_OPENCODE_MAX, False),
        ("2.1.0", False),
        ("nonsense", None),
    ])
    def test_version_in_tested_range(self, version, expected):
        assert version_in_tested_range(version) is expected

    def test_in_range_version_and_no_autoupdate_is_silent(self, monkeypatch):
        monkeypatch.setattr(
            "overcode.backends.opencode.autoupdate_enabled", lambda: False
        )
        assert version_findings("1.18.19") == []

    def test_out_of_range_version_warns(self, monkeypatch):
        monkeypatch.setattr(
            "overcode.backends.opencode.autoupdate_enabled", lambda: None
        )
        findings = version_findings("2.4.0")
        assert len(findings) == 1
        assert "outside the tested range" in findings[0]

    def test_unknown_version_warns(self, monkeypatch):
        monkeypatch.setattr(
            "overcode.backends.opencode.autoupdate_enabled", lambda: None
        )
        findings = version_findings("banana")
        assert len(findings) == 1
        assert "unrecognised" in findings[0]

    def test_autoupdate_warns(self, monkeypatch):
        monkeypatch.setattr(
            "overcode.backends.opencode.autoupdate_enabled", lambda: True
        )
        monkeypatch.setattr(
            "overcode.backends.opencode.global_config_path",
            lambda: Path("/tmp/opencode.json"),
        )
        findings = version_findings("1.18.19")
        assert len(findings) == 1
        assert "autoupdate" in findings[0]

    def test_missing_config_is_silent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert autoupdate_enabled() is None

    def test_config_without_autoupdate_is_silent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = tmp_path / "opencode" / "opencode.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"$schema": "https://opencode.ai/config.json"}')
        assert autoupdate_enabled() is None

    def test_jsonc_with_comments_parses(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = tmp_path / "opencode" / "opencode.jsonc"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('// the installer writes .jsonc\n{"autoupdate": true}\n')
        assert autoupdate_enabled() is True

    def test_malformed_config_is_silent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = tmp_path / "opencode" / "opencode.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{not json at all")
        assert autoupdate_enabled() is None
