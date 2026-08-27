"""grok backend — launch and polling status for xAI's Grok Build CLI.

Phase 3 of ``docs/design/agent-backends-codex-grok.md``: grok agents launch,
monitor via pane polling, instruct, restart, kill, resume and fork — and,
uniquely among overcode's backends so far, prescribe their own session id
and inject a permission allowlist at launch time, since both are
launch-flag-shaped for grok (Claude Code is the only other backend with
either capability, and grok is the first non-Claude one to use overcode's
existing prescribed-session-id path in ``launcher.py``).

Phase 4 adds hooks-grade status (grok's Claude-compatible, camelCase-dialect
hooks system) and the token/cost/context stats ``updates.jsonl`` makes
available. ``prepare_launch()`` stays a no-op until then.

Everything below the flag table was captured from a real Grok Build v1.0.5
session during Phase 0 live verification; the pane corpus lives in
``tests/fixtures_grok_panes/`` and is replayed by
``tests/unit/test_status_detector_grok.py``. Appendix B of the design doc is
the authority for every gesture and flag asserted here.
"""

import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from ..exceptions import AgentCliNotFoundError
from ..status_patterns import StatusPatterns
from .base import (
    BackendCapability,
    DialogRule,
    KeyPress,
    LaunchSpec,
)

if TYPE_CHECKING:
    from ..stats_reader import StatsReader


class GrokNotFoundError(AgentCliNotFoundError):
    """Raised when the grok CLI isn't on PATH.

    Subclasses ``AgentCliNotFoundError`` (aka ``ClaudeNotFoundError``) so the
    launcher's existing "agent CLI missing" handling catches it without a new
    except clause.
    """


# Grok Build v1.0.5 is what Appendix B was verified against. Like codex,
# there is no evidence of a fast-churning release cadence in Phase 0
# (`grok update --help` and config.toml show no auto-update toggle at all),
# but it is still worth a doctor warning once a real version drifts far
# enough that the corpus might not describe it anymore.
TESTED_GROK_MIN = "1.0.5"
TESTED_GROK_MAX = "2.0.0"              # exclusive upper bound
TESTED_GROK_RANGE = (TESTED_GROK_MIN, TESTED_GROK_MAX)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# A regex that can never match, for Claude/opencode/codex concepts grok has
# no analogue for (plan-mode approval, subagent/monitor/background-bash
# counts, auto-accept bar).
_NEVER = r"(?!)"
# Same idea for substring fields — NUL never appears in captured pane text.
_NEVER_SUBSTRING = "\x00"

# The input box's empty-input line, e.g.
#   │ ❯                                                            │
# Box width is terminal-dependent, so this can't be a fixed string the way
# codex's "Ask Codex to do anything" placeholder is — it's matched as a
# shape instead: a light-vertical-bar-bordered box containing a bare "❯"
# with nothing but whitespace on either side. Deliberately anchored on "│"
# (U+2502, the input box's border) rather than "┃" (U+2503), which is the
# *permission dialog's* quote-box glyph — the two never collide.
_EMPTY_INPUT_BOX_RE = re.compile(r"^│\s*❯\s*│$")


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract a (major, minor, patch) tuple from `grok --version` output.

    Returns None when nothing version-shaped is present.
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_in_tested_range(version: str) -> Optional[bool]:
    """True/False when `version` is inside TESTED_GROK_RANGE, None if unparseable."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    low = parse_version(TESTED_GROK_MIN)
    high = parse_version(TESTED_GROK_MAX)
    return low <= parsed < high


def auth_file_path() -> Path:
    """Where grok stores its login credentials."""
    return Path.home() / ".grok" / "auth.json"


def auth_missing() -> bool:
    """True when grok's auth file is absent or empty — `grok login` never ran.

    A present-but-empty file is treated the same as absent: either way grok
    launches will fail with an auth error, and the doctor finding should
    name `grok login` in both cases rather than only the missing-file one.
    """
    path = auth_file_path()
    try:
        return (not path.exists()) or path.stat().st_size == 0
    except OSError:
        return True


class GrokStatusPatterns(StatusPatterns):
    """grok chrome, with the busy/ready predicates widened like its siblings.

    grok draws its input as a box (``│`` border) with a persistent telemetry
    opt-in banner and a dynamic git-branch/token-counter header above it —
    verified in the corpus, the busy footer hint line
    (``Shift+Tab:mode │ Esc:cancel │ Ctrl+x:shortcuts``) sits exactly at the
    pane's trailing edge whether busy or idle, so the base class's tail=2
    default already reaches it; the widening here is for
    ``is_input_ready()``, whose empty-box marker can sit several lines above
    the bottom edge on a fresh (pre-interaction) launch, where the banner
    box pushes the mode-hint footer off screen entirely (see
    ``idle_fresh.txt`` — no hint bar at all, only a right-aligned
    ``[stable]`` channel tag).
    """

    def is_busy(self, lines: List[str], tail: int = 10) -> bool:
        return super().is_busy(lines, tail=tail)

    def is_input_ready(self, lines: List[str], tail: int = 10) -> bool:
        if super().is_input_ready(lines, tail=tail):
            return True
        window = lines[-tail:] if tail else lines
        for line in window:
            if _EMPTY_INPUT_BOX_RE.match(line.strip()):
                return True
        return any(self.shows_input_hint(line) for line in window)


GROK_PATTERNS = GrokStatusPatterns(
    # ── Permission dialog ────────────────────────────────────────────────
    # From permission_required.txt:
    #   ┃  1 (●) Yes, and don't ask again for anything (always-approve mode)
    #   ┃  2 (○) Yes, proceed
    #   ┃  3 (○) No, reject (type to add feedback)
    #   1/3:select │ Tab:next option │ Ctrl+o:always-approve │ Ctrl+c:cancel │ Esc:scrollback
    # None of these strings appear in the busy/idle footer, so they can't
    # collide with active_indicators/busy_markers below.
    permission_patterns=[
        "yes, and don't ask again for anything",
        "no, reject",
        "1/3:select",
        "esc:scrollback",
    ],

    # ── Busy ─────────────────────────────────────────────────────────────
    # busy.txt: the mode-hint footer swaps "Ctrl+x:shortcuts" for
    # "Esc:cancel │ Ctrl+x:shortcuts" only while a turn is in flight — this
    # is the one bit of chrome the idle and busy footers actually differ on
    # (both otherwise show the same "Shift+Tab:mode" prefix). The spinner
    # line itself ("⠼ Waiting for response… 0.9s ... [stop]") is also
    # captured for extra robustness, though it can scroll out of the
    # 10-line tail on a longer turn — the footer hint never does, since it's
    # fixed UI chrome at the pane's trailing edge.
    active_indicators=[
        "esc:cancel",
        "waiting for response",
        "[stop]",
    ],

    # grok's own status/response lines ("◆ Thought for 0.1s", "◆ Run Print
    # hello to stdout") are past-tense once a tool call finishes and stay on
    # screen — matching them as "still executing" would report a settled
    # agent as running, so (like opencode/codex) tool-execution detection is
    # deliberately disabled; the footer hint already covers the in-flight
    # case via busy_markers/active_indicators.
    execution_indicators=[],

    waiting_patterns=[
        "do you want",
        "proceed",
        "yes/no",
        "[y/n]",
        "press any key",
    ],

    # The input box's leading glyph. Never appears bare on its own line —
    # grok always pairs it with the box's "│" border — see
    # GrokStatusPatterns.is_input_ready for how idle is actually detected.
    prompt_chars=["❯"],
    line_prefixes=["❯ ", "◆ ", "┃  ◆ ", "┃  ", "- "],

    # The header line (git branch + cwd + token counter) and the input
    # box's top/bottom borders are the chrome with a stable prefix; the
    # bottom border also carries the dynamic "· always-approve" mode pill,
    # which would otherwise trip the content-change hash every tick.
    status_bar_prefixes=["⎇", "╭", "╰"],

    # command_menu.txt: "  /help    Browse commands and keyboard shortcuts"
    # — the same shape Claude Code's default pattern already matches
    # (leading whitespace, "/name", 2+ spaces, then text), so no grok-
    # specific pattern is needed. Left as the inherited default rather than
    # re-declared, so a future base-class tightening doesn't silently
    # diverge here without also changing this fixture-grounded value.
    command_menu_pattern=r"^\s*/[\w-]+\s{2,}\S",

    spawn_failure_patterns=[
        "command not found",
        "not found:",
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",
    ],

    # grok has no plan-mode / "approve this plan" stage.
    approval_patterns=[_NEVER],

    # Supervisor-daemon fields. The supervisor's own meta-agent stays Claude
    # (design doc predecessor §2.4), so these are only meaningful if that
    # ever changes.
    daemon_active_indicators=["esc:cancel", "waiting for response"],
    daemon_tool_indicators=["◆ ", "┃  ◆ "],

    # UNVERIFIED: error_bad_model.txt is a *headless* (`-p`/`--single`)
    # capture, not a live TUI error state — the interactive TUI was
    # confirmed to swallow a bad --model id silently (README, "Findings
    # that surprised us"), so no live TUI error chrome exists in the Phase 0
    # corpus to ground a pattern on. Left empty rather than guessed.
    error_patterns=[],

    permission_chrome_markers=[
        "1/3:select",
        "tab:next option",
        "ctrl+o:always-approve",
        "ctrl+c:cancel",
        "esc:scrollback",
    ],

    # "◆ Thought for 0.1s" / "◆ Run Print hello to stdout" are grok's own
    # status/response lines, always led by this glyph (optionally nested one
    # level inside the permission dialog's "┃" quote box).
    tool_output_prefixes=["◆ ", "┃  ◆ "],
    tool_output_marker="◆",

    busy_markers=["esc:cancel", "waiting for response", "[stop]"],

    # The input box's top border — present in every live pane (fresh,
    # post-interaction, mid-turn, even the never-visited-directory trust
    # check) and absent the moment the process exits (exited_shell.txt,
    # error_bad_model.txt have no box at all). This is what
    # GrokStatusPatterns.is_input_ready falls back to alongside the
    # empty-box regex.
    input_hint_markers=["╭"],

    # UNVERIFIED: no reasoning-capable model rendered visible thinking
    # chrome during Phase 0 corpus capture (the "◆ Thought for Ns" line is
    # a settled summary, not a live spinner).
    thinking_markers=[],

    # A single space follows "❯" in every captured line (echoed prompts,
    # empty box, slash-menu selection) — no non-breaking space observed.
    prompt_continuation_chars=[" "],

    # No "↵ to send" style autocomplete hint exists in grok's input box.
    # command_menu.txt's "Enter:send" hint is footer chrome that appears
    # only while the slash-menu is open, not an inline autocomplete overlay
    # on a partially-typed line — a different concept from Claude's hint,
    # so it is not wired here either.
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,

    # Hook-detector-only field (Phase 4 wiring): downgrades a stuck RUNNING
    # to waiting_user when the user interrupted the turn from the keyboard.
    # interrupted.txt: "Turn cancelled by user in 4.3s.", left on screen
    # with the process alive — the trailing duration varies, so the marker
    # omits it.
    interrupt_prompt_markers=["Turn cancelled by user"],

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

    # No analogue: grok's footer carries the model/effort/mode pill and a
    # token counter, never counts of background bashes, subagents,
    # monitors, or an auto-accept toggle.
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)


class GrokBackend:
    """Grok Build CLI adapter (verified against v1.0.5, Phase 0 of the design doc).

    RESUME, FORK, SESSION_ID_PRESCRIPTION and PERMISSION_INJECTION as of
    Phase 3 — all four are launch-flag-shaped for grok, unlike codex (which
    has neither) or opencode (which mints its own session ids and has no
    tool-allowlist flag). Still no HOOK_EVENTS / TRANSCRIPT_STATS (Phase 4
    adds grok's Claude-compatible, camelCase-dialect hooks and the
    ``updates.jsonl`` token/cost split), no SKILLS / SANDBOX_PROBE /
    SUBSCRIPTION_USAGE / AGENT_TEAMS.
    """

    name = "grok"
    display_name = "grok"
    binary = "grok"
    version_args = ("--version",)
    install_hint = (
        "Grok Build CLI is required but not found. "
        "Install it with: curl -fsSL https://x.ai/cli/install.sh | bash "
        "(requires a SuperGrok or X Premium+ subscription)"
    )
    # No wrapper/child split like codex's npm shim — `grok` is the process
    # basename directly, confirmed via `pgrep -fl grok` during Phase 0.
    process_basenames = ("grok",)
    not_found_error = GrokNotFoundError
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.FORK
        | BackendCapability.SESSION_ID_PRESCRIPTION
        | BackendCapability.PERMISSION_INJECTION
    )
    # grok's fork grammar takes an explicit new --session-id alongside
    # --fork-session, and that id is authoritative (verified live — the
    # forked session lands at sessions/<enc-cwd>/<new-uuid>/, not one grok
    # chose itself). Unlike Claude Code, which also declares
    # SESSION_ID_PRESCRIPTION but mints its own different id on fork, grok
    # needs launcher.py to mint and bind the id eagerly. See
    # AgentBackend.fork_prescribes_new_session_id's docstring in base.py.
    fork_prescribes_new_session_id = True

    def executable(self) -> str:
        """The binary to invoke, honouring the GROK_COMMAND override.

        Mirrors CLAUDE_COMMAND/OPENCODE_COMMAND/CODEX_COMMAND: the override
        is how the e2e mock harness substitutes a fake TUI.
        """
        return os.environ.get("GROK_COMMAND", self.binary)

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        args = ["--resume", session_id]
        if fork:
            args.append("--fork-session")
        return args

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """Construct the grok CLI argument list.

        Flag mapping (Appendix B of the design doc, verified at v1.0.5):
          fresh              -> ``grok --session-id <uuid> [opts]`` when a
                                 session id was prescribed, else ``grok [opts]``
          resume             -> ``grok --resume <id> [opts]``
          fork               -> ``grok --resume <id> --fork-session
                                 --session-id <new-uuid> [opts]`` — the fork's
                                 id is prescribed too (launcher.py mints one
                                 for any SESSION_ID_PRESCRIPTION backend)
          model              -> ``-m <id>`` (bare id, e.g. ``grok-4.6``)
          persona            -> ``--agent <name>``
          bypass             -> ``--permission-mode bypassPermissions``
          permissive         -> ``--permission-mode auto`` (Phase 0 correction
                                 — ``dontAsk`` shows the identical dialog as
                                 ``default``, only ``auto`` actually skips it)
          normal             -> ``--permission-mode default``

        The permission mode is passed EXPLICITLY on every single launch,
        never omitted: the user's own ``~/.grok/config.toml`` can set
        ``[ui] permission_mode = "always-approve"``, and Phase 0 confirmed
        live that the flag overrides it (a dialog appeared under
        ``--permission-mode default`` despite the config) — omitting the
        flag would silently defer to whatever the user's config says,
        making overcode's three permission modes meaningless.

        ``allowed_tools`` (comma-separated bare tool names, e.g.
        ``"Bash,Read"``) becomes one repeated ``--allow <name>`` per tool —
        Phase 0 confirmed ``--allow`` takes Claude's ``Tool(glob)`` rule
        grammar and actually suppresses the dialog live; a bare tool name is
        the parent case of that grammar (allow every invocation of that
        tool), the same meaning Claude's own ``--allowedTools`` gives a bare
        name.

        ``--fullscreen`` is passed on every launch per the corpus README's
        recommendation: chrome is byte-identical to the default on this
        account, but a user's own ``[ui] screen_mode = "minimal"`` config
        would otherwise switch to grok's scrollback-native renderer — a
        fundamentally different chrome that would silently break every
        ``GROK_PATTERNS`` regex. The flag is documented as session-scoped
        only (no config write), so it is free determinism.
        """
        cmd = [self.executable()]

        if spec.resume_session_id:
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))
            if spec.fork and spec.prescribed_session_id:
                cmd.extend(["--session-id", spec.prescribed_session_id])
        elif spec.prescribed_session_id:
            cmd.extend(["--session-id", spec.prescribed_session_id])

        cmd.append("--fullscreen")

        if spec.model:
            cmd.extend(["-m", spec.model])

        if spec.agent:
            cmd.extend(["--agent", spec.agent])

        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            mode = "bypassPermissions"
        elif spec.skip_permissions or spec.permissiveness_mode == "permissive":
            mode = "auto"
        else:
            mode = "default"
        cmd.extend(["--permission-mode", mode])

        if spec.allowed_tools:
            for rule in spec.allowed_tools.split(","):
                rule = rule.strip()
                if rule:
                    cmd.extend(["--allow", rule])

        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))

        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        """No side effects this phase — telemetry injection is Phase 4."""
        return None

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        """grok reads its x.ai auth (~/.grok/auth.json) ambiently.

        Nothing to forward this phase — Phase 4 adds the hook-injection
        env/config wiring.
        """
        return {}

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Bare C-c is confirmed SAFE on grok (interrupts only, process and
        # session stay alive) — the opposite result from codex/opencode.
        # overcode still prefers the slash command over C-c per Appendix B's
        # recommendation: a single Escape settles any in-flight turn first
        # ("Turn cancelled by user in 4.3s.", no second press needed), then
        # /quit cleanly exits and prints a `grok --resume <uuid>` hint.
        return [
            KeyPress("Escape", enter=False, delay_after=0.5),
            KeyPress("/quit", enter=True),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        # "/new  Start a new session" — verified present in the `/` command
        # menu (command_menu.txt). There is no literal "/clear".
        return [KeyPress("/new", enter=True)]

    def approve_keys(self) -> List[KeyPress]:
        # permission_required.txt: option 1 ("Yes, and don't ask again for
        # anything") is default-selected, but that's grok's always-approve
        # mode switch, not a one-time approval — sending it would silently
        # change the session's permission mode. Option 2 ("Yes, proceed")
        # is the one-time approve; pressing its digit alone executes
        # immediately, no Enter needed (confirmed live).
        return [KeyPress("2", enter=False)]

    def reject_keys(self) -> List[KeyPress]:
        # Option 3 ("No, reject") — same digit-alone-executes behavior.
        return [KeyPress("3", enter=False)]

    def startup_dialog_rules(self) -> List[DialogRule]:
        # None observed: trust_dialog.txt (a plain `grok` launch in a
        # never-before-visited, git-initialized scratch dir) is chrome-
        # identical to idle_fresh.txt — no trust prompt at all. The
        # "Help improve Grok [Opt out] [Opt in]" telemetry banner visible in
        # every capture is passive chrome, not a blocking dialog: it never
        # changes shape or disappears across any fixture, busy or idle.
        return []

    def prompt_ready_chars(self) -> Set[str]:
        # grok never draws a bare prompt glyph either — like codex, it's
        # always inside box-drawing chrome — but unlike codex's fixed-width
        # placeholder text, grok's box border width depends on the terminal,
        # so no single input-line string is exact-match-stable across
        # terminal sizes. The one width-invariant exact match actually
        # captured live is the right-aligned release-channel tag
        # ("[stable]") that sits alone on its own line on a fresh,
        # pre-interaction launch — after `.strip()` it's a fixed string
        # regardless of how much leading padding preceded it. This only
        # covers the moment `_wait_for_prompt()` actually needs (right after
        # spawn, before the first interaction); if a channel other than
        # stable ever renders here, this degrades to the launcher's
        # existing 30s-timeout-then-send-anyway fallback, not a crash.
        return {"[stable]"}

    def status_patterns(self) -> StatusPatterns:
        return GROK_PATTERNS

    def make_stats_reader(self) -> "StatsReader":
        # TRANSCRIPT_STATS isn't declared yet (Phase 4), so this is never
        # actually called by stats_reader_for_session() — defined anyway for
        # protocol completeness, same posture Claude/codex/opencode take.
        from ..stats_reader import NullStatsReader
        return NullStatsReader(self.name)

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        """A live grok process is all argv alone can tell us this phase.

        Phase 4 adds the hooks-injection artifact this will actually
        inspect (grok's ``~/.grok/hooks/overcode.json``, the way codex's
        verdict inspects ``--dangerously-bypass-hook-trust`` in argv and
        opencode's ``refine_health_verdict`` inspects the project's plugin
        file). Until then, a running process is healthy by construction.
        """
        from ..doctor import VERDICT_OK
        return VERDICT_OK, "grok process running"

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


_backend: Optional[GrokBackend] = None


def get_grok_backend() -> GrokBackend:
    """Module-level singleton — backends are stateless."""
    global _backend
    if _backend is None:
        _backend = GrokBackend()
    return _backend


def installed_version() -> Optional[str]:
    """Run `grok --version`, returning the trimmed output or None.

    Always probes the real binary, never GROK_COMMAND — a doctor check
    against the mock harness would be meaningless.
    """
    from ..dependency_check import check_agent_cli

    available, _path, version = check_agent_cli(get_grok_backend())
    if not available or not version:
        return None
    return version.strip() or None


def version_findings(version: Optional[str] = None) -> List[str]:
    """Doctor warnings about the installed grok, newest concern first.

    Returns human-readable strings (empty when everything looks fine) so the
    CLI can print them without importing version-comparison logic.
    """
    findings: List[str] = []

    resolved = version if version is not None else installed_version()
    if resolved is None:
        findings.append(
            "could not determine the installed grok version "
            "(`grok --version` failed) — overcode is tested against "
            f"{TESTED_GROK_MIN} <= version < {TESTED_GROK_MAX}"
        )
        return findings

    in_range = version_in_tested_range(resolved)
    if in_range is None:
        findings.append(
            f"unrecognised grok version string '{resolved}' — overcode "
            f"is tested against {TESTED_GROK_MIN} <= version < "
            f"{TESTED_GROK_MAX}"
        )
    elif not in_range:
        findings.append(
            f"grok {resolved} is outside the tested range "
            f"{TESTED_GROK_MIN} <= version < {TESTED_GROK_MAX} — "
            "status detection reads the TUI's on-screen chrome and may drift"
        )

    # Only meaningful once we know the binary actually runs — otherwise this
    # would misreport "binary found" underneath the "could not determine
    # version" finding above.
    if auth_missing():
        findings.append(
            f"grok {resolved} is installed but {auth_file_path()} is missing "
            "or empty — launches will fail until you run `grok login` "
            "(requires a SuperGrok or X Premium+ subscription)"
        )

    return findings


__all__ = [
    "GROK_PATTERNS",
    "GrokBackend",
    "GrokNotFoundError",
    "GrokStatusPatterns",
    "TESTED_GROK_MAX",
    "TESTED_GROK_MIN",
    "TESTED_GROK_RANGE",
    "auth_file_path",
    "auth_missing",
    "get_grok_backend",
    "installed_version",
    "parse_version",
    "version_findings",
    "version_in_tested_range",
]
