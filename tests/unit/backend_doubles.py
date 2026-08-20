"""Registerable backend doubles for Phase 3 (per-session status detection).

These stand in for a second agent CLI: a different prompt char, different
tool-output glyph, different busy/permission chrome. They exist to prove the
detection stack reads everything from ``StatusPatterns`` rather than from
hardcoded Claude Code strings.
"""

from overcode.backends import BackendCapability
from overcode.backends.claude_code import ClaudeCodeBackend
from overcode.status_patterns import StatusPatterns


def make_test_patterns() -> StatusPatterns:
    """A pattern set that shares no chrome with Claude Code."""
    return StatusPatterns(
        permission_patterns=["permission required", "allow once"],
        active_indicators=["ctrl+c to stop", "◍"],
        execution_indicators=["Fetching", "Reading"],
        prompt_chars=["»"],
        line_prefixes=["» "],
        status_bar_prefixes=["▤"],
        command_menu_pattern=r"^\s*:[\w-]+\s{2,}\S",
        tool_output_prefixes=["▸ ", "▸  "],
        tool_output_marker="▸",
        busy_markers=["ctrl+c to stop"],
        input_hint_markers=["? for help"],
        thinking_markers=["reasoning"],
        prompt_continuation_chars=[" "],
        autocomplete_hint_symbol="⏎",
        autocomplete_hint_word="submit",
        interrupt_prompt_markers=["Cancelled by user"],
        background_bash_count_pattern=r"(\d+)\s+jobs",
        background_bash_marker="jobs",
        single_task_running_marker="(busy)",
        subagent_count_pattern=r"(\d+)\s+workers?",
        monitor_count_pattern=r"(\d+)\s+watchers?\b",
        auto_accept_pattern=r"▤\s+auto-run",
    )


class HookedTestBackend(ClaudeCodeBackend):
    """Second backend with its own chrome, able to emit hook-state files."""

    name = "__hooked_test__"
    display_name = "Hooked Test"
    binary = "hookedtest"
    process_basenames = ("hookedtest",)
    capabilities = BackendCapability.RESUME | BackendCapability.HOOK_EVENTS

    def status_patterns(self) -> StatusPatterns:
        return _HOOKED_PATTERNS


class PollingOnlyBackend(ClaudeCodeBackend):
    """Second backend with no push telemetry — polling is the only option."""

    name = "__polling_only_test__"
    display_name = "Polling Only Test"
    binary = "pollingonly"
    process_basenames = ("pollingonly",)
    capabilities = BackendCapability.RESUME

    def status_patterns(self) -> StatusPatterns:
        return _POLLING_ONLY_PATTERNS


_HOOKED_PATTERNS = make_test_patterns()
_POLLING_ONLY_PATTERNS = make_test_patterns()
