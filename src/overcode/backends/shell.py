"""shell backend — a plain interactive shell as an overcode row (#496).

Not an agent CLI: the window runs ``$SHELL`` (bash when unset), so a
terminal lives in the agent list beside the agents — selectable,
previewable, attachable, renamable and killable like any of them, with
none of the agent machinery. No capabilities: no transcripts (a
``NullStatsReader``, so token/cost/context columns render placeholders),
no hooks, no resume/fork, no permission or persona injection. The
supervisor leaves shell rows alone (``is_shell_session``).

Launch: the launch line is typed into the tmux window's own shell, so
the backend ``exec``s the new one — ``OVERCODE_*=… exec /bin/zsh`` — so
the pane's process *is* the row's shell. ``exit`` then closes the
window and the row goes terminated, the same lifecycle as an agent whose
CLI exits, rather than dropping back to an outer shell that looks alive
but carries none of the row's environment.

Status comes from tmux's ``pane_current_command`` rather than from pane
text (``ShellStatusDetector``): a shell's prompt is whatever the user's
rc files draw, so scraping for it can't work in general, but the
foreground process can't lie — the shell itself means it is waiting at
its prompt, anything else means a command is running.
"""

import os
import shlex
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from ..exceptions import AgentCliNotFoundError
from ..status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from ..status_detector import PollingStatusDetector
from ..status_patterns import StatusPatterns
from .base import (
    BackendCapability,
    DialogRule,
    KeyPress,
    LaunchSpec,
)

if TYPE_CHECKING:
    from ..stats_reader import StatsReader


SHELL_BACKEND = "shell"

# Basenames tmux reports as ``pane_current_command`` for an interactive
# shell sitting at its prompt. The configured shell's own basename is added
# at runtime (``ShellBackend.process_basenames``), so an unusual $SHELL
# still reads as idle.
KNOWN_SHELLS = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "mksh", "tcsh", "csh",
    "nu", "elvish", "xonsh",
})

# Same idea as codex's: regex/substring values that can never match, for
# the Claude chrome a shell has no analogue of — so a shell's own output
# (a "3 bashes" in a log, a "⏺" in a file) can't light up agent columns.
_NEVER = r"(?!)"
_NEVER_SUBSTRING = "\x00"

SHELL_PATTERNS = StatusPatterns(
    permission_patterns=[],
    active_indicators=[],
    execution_indicators=[],
    waiting_patterns=[],
    prompt_chars=[],
    line_prefixes=[],
    status_bar_prefixes=[],
    command_menu_pattern=_NEVER,
    # A shell reporting "command not found" is the user's typo, not a
    # failed agent spawn.
    spawn_failure_patterns=[],
    approval_patterns=[],
    daemon_active_indicators=[],
    daemon_tool_indicators=[],
    error_patterns=[],
    permission_chrome_markers=[],
    tool_output_prefixes=[],
    tool_output_marker=_NEVER_SUBSTRING,
    busy_markers=[],
    input_hint_markers=[],
    thinking_markers=[],
    prompt_continuation_chars=[],
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,
    interrupt_prompt_markers=[],
    tool_execution_pattern=_NEVER,
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)


def resolve_shell() -> str:
    """The user's login shell ($SHELL), falling back to bash."""
    return os.environ.get("SHELL") or "bash"


def is_shell_session(session: Any) -> bool:
    """True when ``session`` is a plain-shell row rather than an agent."""
    return getattr(session, "backend", None) == SHELL_BACKEND


class ShellNotFoundError(AgentCliNotFoundError):
    """Raised when neither $SHELL nor bash can be found."""


class ShellStatusDetector(PollingStatusDetector):
    """Status from the pane's foreground process, not its text.

    The shell itself in the foreground → waiting at its prompt
    (``waiting_user``: it is waiting on the user, and a command finishing
    rings the same bell an agent finishing a turn does). Anything else →
    running, with the command as the activity. One ``list-panes`` answers
    every shell row, cached for ``LISTING_TTL_SECONDS`` so a TUI polling
    at 4 Hz costs at most one extra tmux command a second, and only while
    a shell row exists. With no listing (tmux could not answer) the row
    reads as waiting — the quiet default.
    """

    LISTING_TTL_SECONDS = 1.0

    def __init__(self, *args, shells: Optional[Set[str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._shells = set(shells) if shells is not None else set(KNOWN_SHELLS)
        self._listing_lock = threading.Lock()
        self._listing: Optional[Dict[str, Any]] = None
        self._listing_at = float("-inf")

    def _panes(self) -> Optional[Dict[str, Any]]:
        with self._listing_lock:
            now = time.monotonic()
            if now - self._listing_at >= self.LISTING_TTL_SECONDS:
                self._listing = self.tmux.list_panes(self.tmux_session)
                self._listing_at = now
            return self._listing

    def foreground_command(self, window: str) -> Optional[str]:
        """``pane_current_command`` for ``window``; None when unknown."""
        from ..tmux_utils import pane_for_window

        panes = self._panes()
        if not panes:
            return None
        pane = pane_for_window(panes, window)
        if pane is None:
            return None
        return pane.current_command or None

    def is_idle_command(self, command: str) -> bool:
        # Login shells can be reported with a leading dash ("-zsh").
        return command.lstrip("-") in self._shells

    def detect_status(self, session, num_lines: int = 0) -> Tuple[str, str, str]:
        terminated, content = self._detect_terminated(session, num_lines)
        if terminated is not None:
            return terminated
        command = self.foreground_command(session.tmux_window)
        if command and not self.is_idle_command(command):
            self._last_detect_phase[session.id] = "shell:foreground"
            return STATUS_RUNNING, f"Running: {command}", content
        self._last_detect_phase[session.id] = "shell:prompt"
        return STATUS_WAITING_USER, "Shell prompt", content


class ShellBackend:
    """A plain interactive shell. See the module docstring."""

    name = SHELL_BACKEND
    display_name = "Shell"
    version_args = ("--version",)
    install_hint = "No shell found: set $SHELL or install bash."
    not_found_error = ShellNotFoundError
    capabilities = BackendCapability.NONE

    @property
    def binary(self) -> str:
        return resolve_shell()

    @property
    def process_basenames(self) -> Tuple[str, ...]:
        # The configured shell first: doctor names it in its "no process"
        # message.
        own = os.path.basename(resolve_shell())
        return (own,) + tuple(sorted(KNOWN_SHELLS - {own}))

    def executable(self) -> str:
        return resolve_shell()

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        return []

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """``exec <shell> [extra args]`` — see the module docstring.

        Every agent-only knob (model, persona, permissions, allowed tools,
        session ids) has no meaning for a shell and is ignored; extra CLI
        args go to the shell (``-l`` for a login shell, say).
        """
        cmd = ["exec", self.executable()]
        for arg in spec.extra_args:
            cmd.extend(shlex.split(arg))
        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        return None

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        return {}

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Not `exit`: that closes the window, and restart/rename relaunch
        # into the same window. Ctrl-C stops a foreground command (and
        # clears a half-typed line) so the relaunch line lands at a prompt;
        # the relaunch's `exec` then replaces the shell in place.
        return [KeyPress("C-c", enter=False, delay_after=0.3)]

    def clear_conversation_keys(self) -> List[KeyPress]:
        return [KeyPress("clear", enter=True)]

    def approve_keys(self) -> List[KeyPress]:
        return []

    def reject_keys(self) -> List[KeyPress]:
        return []

    def startup_dialog_rules(self) -> List[DialogRule]:
        return []

    def prompt_ready_chars(self) -> Set[str]:
        return set()

    def prompt_ready_line(self, line: str) -> bool:
        """Any output at all: an initial ``-p`` command is typed ahead.

        A shell's prompt is whatever the user's rc files draw, so there is
        nothing to wait for — and nothing needs waiting for, because the
        terminal buffers typed input until the shell reads it.
        """
        return bool(line)

    def status_patterns(self) -> StatusPatterns:
        return SHELL_PATTERNS

    def make_polling_detector(self, tmux_session: str, tmux=None, patterns=None) -> ShellStatusDetector:
        """The status detector for shell rows (``status_detector_factory``)."""
        own = os.path.basename(resolve_shell())
        return ShellStatusDetector(
            tmux_session, tmux=tmux, patterns=patterns or SHELL_PATTERNS,
            shells=set(KNOWN_SHELLS) | {own},
        )

    def make_stats_reader(self) -> "StatsReader":
        from ..stats_reader import NullStatsReader
        return NullStatsReader(self.name)

    def health_verdict(self, argv: str) -> Tuple[str, str]:
        from ..doctor import VERDICT_OK
        return VERDICT_OK, "plain shell — no telemetry to inject"

    def uninstall_telemetry(self, project_dir: Optional[str] = None) -> Tuple[bool, str]:
        return True, f"nothing installed on disk for this backend ({self.name})"

    def doctor_findings(self) -> List[str]:
        return []

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


__all__ = [
    "KNOWN_SHELLS",
    "SHELL_BACKEND",
    "SHELL_PATTERNS",
    "ShellBackend",
    "ShellNotFoundError",
    "ShellStatusDetector",
    "is_shell_session",
    "resolve_shell",
]
