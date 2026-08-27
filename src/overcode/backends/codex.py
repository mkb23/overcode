"""codex backend — launch, hooks telemetry and stats for the Codex CLI.

Phase 1 of ``docs/design/agent-backends-codex-grok.md``: codex agents
launch, monitor via pane polling, instruct, restart, kill, resume and fork.

Phase 2 adds hooks-grade status and token/cost/context stats: every launch
registers ``overcode hook-handler`` for codex's hook events via per-launch
``-c 'hooks.<Event>=[...]'`` config overrides plus
``--dangerously-bypass-hook-trust`` (Route 1 of the design doc's Phase 0
injection research — the route that writes zero global config files, unlike
the interactive hook-trust dialog's ``t`` gesture). No files are staged, so
``prepare_launch()`` stays a no-op; the injection lives entirely in
``build_command()``.

Everything below the flag table was captured from a real Codex CLI v0.150.1
session during Phase 0 live verification; the pane corpus lives in
``tests/fixtures_codex_panes/`` and is replayed by
``tests/unit/test_status_detector_codex.py``. Appendix A of the design doc is
the authority for every gesture and flag asserted here.
"""

import os
import re
import shlex
import shutil
import sys
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from ..exceptions import AgentCliNotFoundError
from ..hook_handler import CODEX_HOOK_EVENTS
from ..status_patterns import StatusPatterns
from .base import (
    BackendCapability,
    DialogRule,
    KeyPress,
    LaunchSpec,
)

if TYPE_CHECKING:
    from ..stats_reader import StatsReader


def _resolve_overcode_bin() -> str:
    """Resolve the absolute path to the overcode binary.

    Identical to claude_code.py's helper of the same name — codex's hook
    command needs the exact same "how does the hook subprocess find
    overcode" answer Claude's ``--settings`` injection already relies on
    (covers global/pipx installs via ``shutil.which``, falls back to
    ``python -m overcode.cli`` for uv-run/venv-only installs). Not imported
    from ``claude_code`` to avoid a cross-backend import for one helper;
    kept byte-identical instead so the two never drift apart silently.
    """
    which = shutil.which("overcode")
    if which:
        return which
    return f"{sys.executable} -m overcode.cli"


def _codex_hook_toml_array(command: str) -> str:
    """Render one event's hook array as codex's TOML-value ``-c`` syntax.

    Per Appendix A: ``HookHandlerConfig::Command`` is a bare string, not an
    array like Claude's ``command: [str, ...]`` — the array form fails to
    parse (``invalid type: sequence, expected a string in 'hooks'``).
    ``command`` becomes a double-quoted TOML string, so its own backslashes
    and quotes need escaping before they're embedded.
    """
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{{hooks=[{{type="command",command="{escaped}"}}]}}]'


class CodexNotFoundError(AgentCliNotFoundError):
    """Raised when the codex CLI isn't on PATH.

    Subclasses ``AgentCliNotFoundError`` (aka ``ClaudeNotFoundError``) so the
    launcher's existing "agent CLI missing" handling catches it without a new
    except clause.
    """


# codex ships multiple releases a week (0.148 -> 0.150 in days during Phase 0
# verification), so `overcode doctor` warns when the installed version leaves
# the range these patterns and flags were verified against. Codex is still
# pre-1.0, so a hypothetical 1.0 release is treated as a breaking chrome
# change until re-verified.
TESTED_CODEX_MIN = "0.148.0"
TESTED_CODEX_MAX = "1.0.0"             # exclusive upper bound
TESTED_CODEX_RANGE = (TESTED_CODEX_MIN, TESTED_CODEX_MAX)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# A regex that can never match, for Claude/opencode concepts codex has no
# analogue for (plan-mode approval, slash-command menu chrome — never
# captured, subagent/monitor/background-bash counts, auto-accept bar).
_NEVER = r"(?!)"
# Same idea for substring fields — NUL never appears in captured pane text.
_NEVER_SUBSTRING = "\x00"


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract a (major, minor, patch) tuple from `codex --version` output.

    Returns None when nothing version-shaped is present.
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_in_tested_range(version: str) -> Optional[bool]:
    """True/False when `version` is inside TESTED_CODEX_RANGE, None if unparseable."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    low = parse_version(TESTED_CODEX_MIN)
    high = parse_version(TESTED_CODEX_MAX)
    return low <= parsed < high


class CodexStatusPatterns(StatusPatterns):
    """codex chrome, with both "is the pane bottom busy/ready?" predicates widened.

    codex paints the (dormant) input placeholder — "Ask Codex to do
    anything" — and the model/dir footer *below* the spinner line even while
    a turn is in flight (verified in ``busy.txt``: the busy marker sits 2-3
    non-blank lines above the pane's trailing edge, not at it), and it never
    draws a bare prompt glyph for an empty input — only that placeholder text
    inside the box. Both defaults are default-tail 2/4 in the base class,
    tuned for Claude Code's single-line prompt; codex needs the same
    widening opencode's ``is_input_ready`` override needed, applied to the
    full 10-line window ``detect_status()`` already hands these predicates
    (nothing beyond that window is ever visible to either check, so widening
    to it is bounded, not a blank cheque).
    """

    def is_busy(self, lines: List[str], tail: int = 10) -> bool:
        return super().is_busy(lines, tail=tail)

    def is_input_ready(self, lines: List[str], tail: int = 10) -> bool:
        if super().is_input_ready(lines, tail=tail):
            return True
        return any(self.shows_input_hint(line) for line in lines[-tail:])


CODEX_PATTERNS = CodexStatusPatterns(
    # ── Permission dialog ────────────────────────────────────────────────
    # From permission_required.txt:
    #   Would you like to run the following command?
    #   ...
    #   › 1. Yes, proceed (y)
    #   2. Yes, and don't ask again for commands that start with `...` (p)
    #   3. No, and tell Codex what to do differently (esc)
    #   Press enter to confirm or esc to cancel
    # "Would you like to run..." sits far enough above the dialog's body
    # (Environment/Reason/command lines) that it can fall outside the
    # detector's trailing window on a long command; "Yes, proceed" and the
    # footer hint never do, so those two anchor the match.
    permission_patterns=[
        "would you like to run the following command?",
        "yes, proceed",
        "press enter to confirm or esc to cancel",
    ],

    # ── Busy ─────────────────────────────────────────────────────────────
    # busy.txt: "• Working (1s • esc to interrupt)". codex re-renders this
    # spinner line's elapsed-time counter on every tick, so a static-content
    # poll still needs the marker in active_indicators (Phase 9) as a
    # backstop for when content_changed happens to read False.
    active_indicators=[
        "esc to interrupt",
    ],

    # codex's bullet/status lines ("• Ran curl ...", "• I'll fetch ...")
    # are past-tense once a tool call finishes and stay on screen — matching
    # them as "still executing" would report a settled agent as running, so
    # (like opencode) tool-execution detection is deliberately disabled.
    # "esc to interrupt" already covers the in-flight case via busy_markers.
    execution_indicators=[],

    waiting_patterns=[
        "do you want",
        "proceed",
        "yes/no",
        "[y/n]",
        "press any key",
    ],

    # The input line's leading glyph. Never appears bare — codex always
    # draws placeholder or echoed-prompt text after it — see
    # CodexStatusPatterns.is_input_ready for how idle is actually detected.
    prompt_chars=["›"],
    line_prefixes=["› ", "• ", "  └ ", "- "],

    # No stable prefix exists for the "<model> <effort> · <dir>" footer —
    # unlike Claude's "⏵⏵"/opencode's "╹", the model id and directory both
    # vary, so there is nothing fixed to filter on. Left empty rather than
    # guessed; the footer's text never changes within a session, so leaving
    # it unfiltered from the content hash causes no false "changed" flips.
    status_bar_prefixes=[],

    # UNVERIFIED: no slash-command menu was captured in the Phase 0 corpus.
    command_menu_pattern=_NEVER,

    spawn_failure_patterns=[
        "command not found",
        "not found:",
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",
    ],

    # codex has no plan-mode / "approve this plan" stage.
    approval_patterns=[_NEVER],

    # Supervisor-daemon fields. The supervisor's own meta-agent stays Claude
    # (design doc predecessor §2.4), so these are only meaningful if that
    # ever changes.
    daemon_active_indicators=["esc to interrupt"],
    daemon_tool_indicators=["• ", "■ "],

    # UNVERIFIED: the only error-shaped capture (error_bad_model.txt) is a
    # turn-level failure codex recovers from on its own, settling right back
    # at the "Ask Codex to do anything" prompt in the same frame — its own
    # corpus README documents the expected status as waiting_user, not
    # error. Matching the JSON/`⚠ Model metadata` text here would contradict
    # that ground truth by forcing STATUS_ERROR ahead of the prompt-ready
    # check (Phase 6 runs before Phase 12). Left empty until a fixture shows
    # a codex failure that *doesn't* recover to a ready prompt on its own.
    error_patterns=[],

    permission_chrome_markers=[
        "press enter to confirm",
        "esc to cancel",
        "yes, proceed",
        "yes, and don't ask again",
    ],

    # "• Ran curl ..." / "• Working ..." / "■ Conversation interrupted ..."
    # are codex's own status/response lines; both glyphs mark agent output.
    tool_output_prefixes=["• ", "■ "],
    tool_output_marker="•",

    busy_markers=["esc to interrupt"],

    # The idle placeholder text — present only while the TUI is live and
    # accepting input, never in scrollback after codex exits (exited_shell.txt
    # has no trace of it). This is what CodexStatusPatterns.is_input_ready
    # falls back to, since codex never draws a bare prompt glyph.
    input_hint_markers=["Ask Codex to do anything"],

    # UNVERIFIED: no reasoning-capable model rendered visible thinking
    # chrome during Phase 0 corpus capture.
    thinking_markers=[],

    # A single space follows "›" in every captured line (echoed prompts,
    # placeholder, menu options) — no non-breaking space observed.
    prompt_continuation_chars=[" "],

    # No "↵ to send" style hint exists in codex's input box.
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,

    # Hook-detector-only field (Phase 2 wiring): downgrades a stuck RUNNING
    # to waiting_user when the user interrupted the turn from the keyboard.
    # interrupted.txt: "■ Conversation interrupted - tell the model what to
    # do differently...", left on screen with the process alive. Escape
    # during the permission dialog produces the same marker (Appendix A).
    interrupt_prompt_markers=["Conversation interrupted"],

    # Unused while execution_indicators is empty, but harmless to leave at
    # the inherited default shape.
    tool_execution_pattern=(
        r'^\w+\s+'
        r'(?:'
        r'\w+\('
        r'|"'
        r"|'"
        r'|\S+\.\w{1,10}'
        r'|\S+/'
        r')'
    ),

    # No analogue: codex's footer carries model/dir/effort, never counts of
    # background bashes, subagents, monitors, or an auto-accept toggle.
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)


class CodexBackend:
    """Codex CLI adapter (verified against v0.150.1, Phase 0 of the design doc).

    RESUME, FORK, HOOK_EVENTS and TRANSCRIPT_STATS as of Phase 2: every
    launch injects ``overcode hook-handler`` via ``-c 'hooks.<Event>=...'``
    overrides (``build_command()``), and ``CodexStatsReader`` reads the
    rollout JSONL. Still no SESSION_ID_PRESCRIPTION (codex has no such
    flag), no PERMISSION_INJECTION (``--allowed-tools`` has no codex
    analogue — sandbox modes + ``-c`` config only), no SKILLS /
    SANDBOX_PROBE / SUBSCRIPTION_USAGE / AGENT_TEAMS.
    """

    name = "codex"
    display_name = "codex"
    binary = "codex"
    version_args = ("--version",)
    install_hint = (
        "Codex CLI is required but not found. "
        "Install it with: npm install -g @openai/codex"
    )
    # The top-level process is the npm wrapper (`node .../codex ...`); it
    # execs a vendored binary whose basename is `codex` — the one actually
    # running the TUI. Doctor/health-check process discovery walks the whole
    # descendant tree (see doctor.find_agent_process), so matching just the
    # child's basename is sufficient — matching "node" too would be far too
    # broad. Confirmed via `ps aux`/`pgrep -fl codex` during Phase 0 corpus
    # capture.
    process_basenames = ("codex",)
    not_found_error = CodexNotFoundError
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.FORK
        | BackendCapability.HOOK_EVENTS
        | BackendCapability.TRANSCRIPT_STATS
    )

    def executable(self) -> str:
        """The binary to invoke, honouring the CODEX_COMMAND override.

        Mirrors CLAUDE_COMMAND/OPENCODE_COMMAND: the override is how the e2e
        mock harness substitutes a fake TUI.
        """
        return os.environ.get("CODEX_COMMAND", self.binary)

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        # Resume/fork are subcommands, not flags: `codex resume <id>` /
        # `codex fork <id>` — the first backend whose resume grammar isn't
        # flag-shaped. build_command() splices this *before* the shared
        # options rather than appending it.
        return [("fork" if fork else "resume"), session_id]

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """Construct the codex CLI argument list.

        Flag mapping (Appendix A of the design doc, verified at v0.150.1):
          fresh              -> ``codex [opts]``
          resume             -> ``codex resume <id> [opts]`` (subcommand first)
          fork               -> ``codex fork <id> [opts]`` (subcommand first)
          model              -> ``-m <model>`` (bare id, e.g. ``gpt-5.6-sol``)
          bypass             -> ``--dangerously-bypass-approvals-and-sandbox``
          permissive         -> ``-a never --sandbox workspace-write``
          normal             -> no flags (default: on-request approval)

        ``allowed_tools`` and ``agent`` (persona) have no codex analogue —
        no ``--allowedTools``-shaped flag exists, and codex's ``-p/--profile``
        is a config-layer override, not a persona-by-name flag — so both are
        silently ignored, same posture as opencode.
        ``prescribed_session_id`` is also ignored: codex has no
        ``--session-id``-shaped flag for fresh launches.

        Every launch also injects ``overcode hook-handler`` for codex's hook
        events (§2.3 of the design doc) via one ``-c 'hooks.<Event>=[...]'``
        override per event plus ``--dangerously-bypass-hook-trust`` —
        unconditionally, the same posture Claude's ``--settings`` injection
        takes (never gated on a launch flag; the mock harness tolerates the
        extra argv unconditionally, see tests/mock_codex.py).
        """
        cmd = [self.executable()]

        if spec.resume_session_id:
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))

        if spec.model:
            cmd.extend(["-m", spec.model])

        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif spec.skip_permissions or spec.permissiveness_mode == "permissive":
            cmd.extend(["-a", "never", "--sandbox", "workspace-write"])

        overcode_bin = _resolve_overcode_bin()
        hook_array = _codex_hook_toml_array(f"{overcode_bin} hook-handler")
        for hook_event in CODEX_HOOK_EVENTS:
            cmd.extend(["-c", f"hooks.{hook_event}={hook_array}"])
        cmd.append("--dangerously-bypass-hook-trust")

        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))

        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        """No side effects this phase — telemetry injection is Phase 2."""
        return None

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        """codex reads provider credentials (ChatGPT/API key auth) ambiently.

        Nothing to forward this phase — Phase 2 adds the hook-injection
        env/config wiring.
        """
        return {}

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Ctrl-C kills codex outright — confirmed live on an idle session,
        # process gone within 2s, no confirmation (see
        # tests/fixtures_codex_panes/README.md). The safe interrupt is a
        # single Escape ("■ Conversation interrupted...", process stays
        # alive), then /quit cleanly exits to the shell. Never C-c here.
        return [
            KeyPress("Escape", enter=False, delay_after=0.5),
            KeyPress("/quit", enter=True),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        # "/new  start a new chat during a conversation" — verified present
        # in the `/` command menu (exited_shell.txt's capture conditions).
        return [KeyPress("/new", enter=True)]

    def approve_keys(self) -> List[KeyPress]:
        # "1. Yes, proceed (y)" is default-selected; Enter confirms it.
        return [KeyPress("", enter=True)]

    def reject_keys(self) -> List[KeyPress]:
        # "3. No, and tell Codex what to do differently (esc)" — there is no
        # literal `n` reject key, only Escape.
        return [KeyPress("Escape", enter=False)]

    def startup_dialog_rules(self) -> List[DialogRule]:
        # trust_dialog.txt: "Do you trust the contents of this directory? ...
        # Press enter to continue" — Enter accepts option 1 ("Yes,
        # continue"), which persists trust per-path in ~/.codex/config.toml
        # so a revisited directory shows no dialog at all.
        return [
            DialogRule(
                marker="Do you trust the contents of this directory?",
                presses=[KeyPress("", enter=True)],
                settle_seconds=1.5,
            ),
        ]

    def prompt_ready_chars(self) -> Set[str]:
        # Unlike Claude/opencode, codex never draws a bare "›" — it always
        # pairs the glyph with placeholder or echoed-prompt text, confirmed
        # across every fixture in tests/fixtures_codex_panes/. The launcher's
        # _wait_for_prompt() does an exact-line match against this set, so
        # the literal idle placeholder line is what has to go here — found
        # live during Phase 1 smoke testing: a bare-glyph value left the
        # launcher's dialog-dismiss/send-prompt path spinning until timeout,
        # since that exact line never appears.
        return {"› Ask Codex to do anything"}

    def status_patterns(self) -> StatusPatterns:
        return CODEX_PATTERNS

    def make_stats_reader(self) -> "StatsReader":
        from .codex_stats import CodexStatsReader
        return CodexStatsReader()

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        """Hooks reach codex only via the injected ``-c`` overrides.

        ``--dangerously-bypass-hook-trust`` is the one flag build_command()
        never omits when hooks are injected and never emits otherwise, so —
        exactly like Claude's ``"--settings" in argv`` check — its presence
        alone is enough to know the hook route is live, without parsing the
        ``-c hooks.<Event>=...`` overrides themselves.
        """
        from ..doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK

        if "--dangerously-bypass-hook-trust" in argv:
            return VERDICT_OK, "hooks injected via -c overrides + --dangerously-bypass-hook-trust"
        return VERDICT_MISSING_SETTINGS, (
            "codex running without the hook-injection overrides — hooks will "
            "not fire. Relaunch via `overcode restart` to re-inject."
        )

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


_backend: Optional[CodexBackend] = None


def get_codex_backend() -> CodexBackend:
    """Module-level singleton — backends are stateless."""
    global _backend
    if _backend is None:
        _backend = CodexBackend()
    return _backend


def installed_version() -> Optional[str]:
    """Run `codex --version`, returning the trimmed output or None.

    Always probes the real binary, never CODEX_COMMAND — a doctor check
    against the mock harness would be meaningless.
    """
    from ..dependency_check import check_agent_cli

    available, _path, version = check_agent_cli(get_codex_backend())
    if not available or not version:
        return None
    return version.strip() or None


def version_findings(version: Optional[str] = None) -> List[str]:
    """Doctor warnings about the installed codex, newest concern first.

    Returns human-readable strings (empty when the version looks fine) so
    the CLI can print them without importing version-comparison logic.
    """
    findings: List[str] = []

    resolved = version if version is not None else installed_version()
    if resolved is None:
        findings.append(
            "could not determine the installed codex version "
            "(`codex --version` failed) — overcode is tested against "
            f"{TESTED_CODEX_MIN} <= version < {TESTED_CODEX_MAX}"
        )
    else:
        in_range = version_in_tested_range(resolved)
        if in_range is None:
            findings.append(
                f"unrecognised codex version string '{resolved}' — overcode "
                f"is tested against {TESTED_CODEX_MIN} <= version < "
                f"{TESTED_CODEX_MAX}"
            )
        elif not in_range:
            findings.append(
                f"codex {resolved} is outside the tested range "
                f"{TESTED_CODEX_MIN} <= version < {TESTED_CODEX_MAX} — "
                "status detection reads the TUI's on-screen chrome and may drift"
            )

    # Static, not config-driven: Phase 0 found `codex features list` reports
    # `in_app_updates stable true` (enabled by default) with no config
    # toggle to disable it, unlike opencode's `autoupdate` config key. Always
    # surfaced rather than guessed at from a file that doesn't exist.
    findings.append(
        "codex ships in-app updates enabled by default (`codex features "
        "list` -> `in_app_updates stable true`) — an unattended upgrade can "
        "move the TUI chrome out from under this pattern set; no config "
        "toggle to disable it was found during Phase 0 verification"
    )

    # Rollout JSONL shape drift is the other way a codex upgrade silently
    # blanks the stats columns, so it rides the same doctor pass (mirrors
    # opencode's SQLite schema_findings() call here).
    try:
        from .codex_stats import schema_findings
        findings.extend(schema_findings())
    except Exception:
        pass

    return findings


__all__ = [
    "CODEX_PATTERNS",
    "CodexBackend",
    "CodexNotFoundError",
    "CodexStatusPatterns",
    "TESTED_CODEX_MAX",
    "TESTED_CODEX_MIN",
    "TESTED_CODEX_RANGE",
    "get_codex_backend",
    "installed_version",
    "parse_version",
    "version_findings",
    "version_in_tested_range",
]
