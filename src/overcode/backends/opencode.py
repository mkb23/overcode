"""opencode backend — launch, telemetry and stats for the opencode CLI.

Phases 4-5 of ``docs/design/agent-agnostic-backends-opencode.md``: opencode
agents launch, monitor, instruct, restart, kill and resume (Phase 4), and
report hooks-grade status plus token/cost columns (Phase 5).

Telemetry arrives through a bundled opencode plugin
(``src/overcode/opencode_plugin/overcode-telemetry.js``) that overcode copies
into ``<start_directory>/.opencode/plugins/`` at launch. The plugin writes the
same ``hook_state_<agent>.json`` / ``hook_events_<agent>.jsonl`` files Claude
Code's hooks produce, so ``HookStatusDetector`` works unchanged. Pane polling
remains the fallback for a launch where the plugin could not be installed.

Everything below the flag table was captured from a real opencode
v1.18.19 session; the pane corpus lives in
``tests/fixtures_opencode_panes/`` and is replayed by
``tests/unit/test_status_detector_opencode.py``.
"""

import json
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


class OpencodeNotFoundError(AgentCliNotFoundError):
    """Raised when the opencode CLI isn't on PATH.

    Subclasses ``AgentCliNotFoundError`` (aka ``ClaudeNotFoundError``) so the
    launcher's existing "agent CLI missing" handling catches it without a new
    except clause.
    """


# opencode ships every 2-3 days and moved its storage layout at v1.2.0, so
# `overcode doctor` warns when the installed version leaves the range these
# patterns and flags were verified against.
TESTED_OPENCODE_MIN = "1.18.0"
TESTED_OPENCODE_MAX = "2.0.0"          # exclusive upper bound
TESTED_OPENCODE_RANGE = (TESTED_OPENCODE_MIN, TESTED_OPENCODE_MAX)

# Where opencode keeps its global config. Both spellings are real: the
# installer writes .jsonc, the docs describe .json.
OPENCODE_GLOBAL_CONFIG_NAMES = ("opencode.jsonc", "opencode.json")

# The bundled telemetry plugin, and where opencode looks for project plugins.
# Both `.opencode/plugin/` and `.opencode/plugins/` are honoured by v1.18.19;
# the plural is what the docs use.
PLUGIN_FILENAME = "overcode-telemetry.js"
PLUGIN_DIR_PARTS = (".opencode", "plugins")
# A line inside the bundled file that identifies it as overcode's. A file
# without it is the user's own and is never touched.
PLUGIN_MARKER = "OVERCODE-PLUGIN-MARKER: overcode-telemetry"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# A regex that can never match, for the Claude concepts opencode has no
# analogue for (plan-mode approval, subagent/monitor counts, auto-accept bar).
_NEVER = r"(?!)"
# Same idea for substring fields — NUL never appears in captured pane text.
_NEVER_SUBSTRING = "\x00"

# Ancillary (post-Phase-5) true bypass: every per-tool key opencode's own
# published config schema (https://opencode.ai/config.json,
# `$defs.PermissionConfig`) recognizes, each forced to "allow". Live-verified
# Aug 28, 2026 (opencode v1.18.23) via `opencode debug config`: a scratch
# project's `opencode.json` setting `{"bash":"deny","edit":"deny",
# "webfetch":"deny"}`, launched with `OPENCODE_PERMISSION` carrying this exact
# key set at "allow", produced a resolved config with every key "allow" —
# project-level deny rules did not win. A `"*"` wildcard key was also tried
# and did **not** override the explicit deny keys (it was merged in
# alongside them, inert) — confirming the design doc's caution that only
# explicit per-tool keys are the verified-safe grammar.
OPENCODE_ALLOW_EVERYTHING_PERMISSION: Dict[str, str] = {
    key: "allow"
    for key in (
        "read", "edit", "glob", "grep", "list", "bash", "task",
        "external_directory", "lsp", "skill", "todowrite", "question",
        "webfetch", "websearch", "doom_loop",
    )
}


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract a (major, minor, patch) tuple from `opencode --version` output.

    Returns None when nothing version-shaped is present.
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_in_tested_range(version: str) -> Optional[bool]:
    """True/False when `version` is inside TESTED_OPENCODE_RANGE, None if unparseable."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    low = parse_version(TESTED_OPENCODE_MIN)
    high = parse_version(TESTED_OPENCODE_MAX)
    return low <= parsed < high


def global_config_path() -> Optional[Path]:
    """Path to the user's global opencode config, or None when absent."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    for name in OPENCODE_GLOBAL_CONFIG_NAMES:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def autoupdate_enabled() -> Optional[bool]:
    """Best-effort read of `autoupdate` from the global opencode config.

    Returns None when there is no config, it can't be read, or it doesn't
    mention autoupdate — callers stay silent in all three cases.
    """
    path = global_config_path()
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # .jsonc allows comments; strip line comments before parsing.
    stripped = re.sub(r"^\s*//.*$", "", raw, flags=re.MULTILINE)
    try:
        config = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(config, dict) or "autoupdate" not in config:
        return None
    return bool(config["autoupdate"])


def bundled_plugin_path() -> Path:
    """Path to the telemetry plugin shipped inside the overcode package."""
    return Path(__file__).parent.parent / "opencode_plugin" / PLUGIN_FILENAME


def project_plugin_path(start_directory: str) -> Path:
    """Where the plugin has to live for a project-scoped opencode launch."""
    return Path(start_directory).joinpath(*PLUGIN_DIR_PARTS) / PLUGIN_FILENAME


def ensure_plugin_installed(start_directory: Optional[str]) -> Optional[Path]:
    """Copy the telemetry plugin into ``<start_directory>/.opencode/plugins/``.

    Chosen over registering the plugin in the user's global opencode config
    because a global entry would load overcode's telemetry into *every*
    opencode session the user ever runs. A project-local copy is scoped to the
    directory overcode launched in, and the plugin itself no-ops without the
    ``OVERCODE_*`` env vars anyway, so even a stray copy is inert.

    Idempotent and non-destructive:

    * missing            → written
    * present, ours      → rewritten when the bundled version has moved on
    * present, ours, same→ left alone
    * present, not ours  → left alone (the user replaced it deliberately)

    The file is *not* removed when the agent dies: the next launch in that
    directory needs it, and re-ensuring is cheaper than a teardown race. It is
    visible to ``git status`` as an untracked file; overcode deliberately does
    not edit the user's ``.gitignore`` (see ``docs/backends.md``).

    Returns the installed path, or None when nothing could be installed.
    """
    if not start_directory:
        return None
    source = bundled_plugin_path()
    try:
        content = source.read_text(encoding="utf-8")
    except OSError:
        return None

    target = project_plugin_path(start_directory)
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        existing = None

    if existing is not None:
        if PLUGIN_MARKER not in existing:
            return None
        if existing == content:
            return target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        return None
    return target


def plugin_installed(start_directory: Optional[str]) -> bool:
    """True when this project directory carries overcode's telemetry plugin."""
    if not start_directory:
        return False
    try:
        return PLUGIN_MARKER in project_plugin_path(start_directory).read_text(
            encoding="utf-8"
        )
    except OSError:
        return False


class OpencodeStatusPatterns(StatusPatterns):
    """opencode chrome, with the "is the prompt ready?" predicate widened.

    opencode draws its input as a box (``┃`` gutter) rather than a single
    prompt line, and parks a model footer plus a bottom info bar under it,
    so the bare ``┃`` that means "empty input" sits well above the 4-line
    window Claude's default scans. On a *fresh* launch it is further away
    still: the box is vertically centred with blank filler beneath, and the
    detector's bottom-10-lines slice never reaches it at all.

    Hence two widenings: a deeper scan, and a fallback on the hint bar,
    which opencode draws only while the TUI is accepting input. Callers
    reach this predicate only after ruling out a busy pane, so "live and
    not working" genuinely means "waiting for the user".
    """

    def is_input_ready(self, lines: List[str], tail: int = 8) -> bool:
        if super().is_input_ready(lines, tail=tail):
            return True
        return any(self.shows_input_hint(line) for line in lines)


OPENCODE_PATTERNS = OpencodeStatusPatterns(
    # ── Permission dialog ────────────────────────────────────────────────
    # △ Permission required
    #   # Shell command
    #  $ echo hello
    #   Allow once   Allow always   Reject      ⇆ select  enter confirm
    permission_patterns=[
        "permission required",
        "allow once",
        "allow always",
        "enter confirm",
    ],

    # ── Busy ─────────────────────────────────────────────────────────────
    # Bottom bar while a turn is in flight:
    #   ⬝⬝⬝■■■⬝⬝  esc interrupt          tab agents  ctrl+p commands
    # One Escape arms the interrupt and the hint becomes "esc again to
    # interrupt"; a second Escape actually cancels the turn.
    active_indicators=[
        "esc interrupt",
        "esc again to interrupt",
    ],

    # opencode renders finished tool calls as "→ Read README.md" /
    # "✱ Glob \"*\" in ." — present-tense nouns that stay on screen after
    # the turn ends. Matching them would report a settled agent as running,
    # so tool-execution detection is deliberately disabled; "esc interrupt"
    # already covers the in-flight case.
    execution_indicators=[],

    waiting_patterns=[
        "do you want",
        "proceed",
        "yes/no",
        "[y/n]",
        "press any key",
    ],

    # The input box gutter. A bare "┃" line is an empty input.
    prompt_chars=["┃"],
    line_prefixes=["┃  ", "┃ ", "▣  ", "▣ ", "→ ", "✱ ", "- ", "• "],

    # Only the input box's bottom border is unambiguous chrome. The info bar
    # below it starts with the project directory, which has no stable prefix.
    status_bar_prefixes=["╹"],

    # Slash menu rows are drawn inside the box: "┃ /exit    Exit the app   ┃"
    command_menu_pattern=r"^\s*┃?\s*/[\w-]+\s{2,}\S",

    spawn_failure_patterns=[
        "command not found",
        "not found:",
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",
    ],

    # opencode has no plan-mode / "approve this plan" stage.
    approval_patterns=[_NEVER],

    # Supervisor-daemon fields. The supervisor's own meta-agent stays Claude
    # (design §2.4), so these are only meaningful if that ever changes.
    daemon_active_indicators=["esc interrupt", "esc again to interrupt"],
    daemon_tool_indicators=["▣", "→ ", "✱ ", "$ "],

    # opencode renders provider/tool errors as prose inside a red-bordered
    # box using the same ┃ gutter as everything else — the colour is the only
    # structural signal and ANSI is stripped before matching. These are the
    # message texts that are worth claiming; anything else degrades to
    # waiting_user, which is honest ("stopped, needs you").
    error_patterns=[
        r"Incorrect API key provided",
        r"Invalid API key",
        r"No API key",
        r"AI_APICallError",
        r"ProviderAuthError",
        r"\bECONNREFUSED\b",
        r"\bECONNRESET\b",
        r"rate limit (?:exceeded|reached)",
        r"Insufficient credits",
    ],

    permission_chrome_markers=[
        "enter confirm",
        "⇆ select",
        "ctrl+f fullscreen",
    ],

    # "▣  Build · GPT-4o mini · 6.0s" closes each assistant turn; the arrow
    # and asterisk head individual tool calls.
    tool_output_prefixes=["▣  ", "▣ ", "→ ", "✱ ", "$ "],
    tool_output_marker="▣",

    busy_markers=["esc interrupt", "esc again to interrupt"],

    # Present in every live opencode pane, gone once the TUI exits — used to
    # rule out a shell-prompt false positive. "╹▀" is the input box's bottom
    # border, which survives even when a narrow pane truncates the hints.
    input_hint_markers=["ctrl+p commands", "tab agents", "╹▀"],

    # UNVERIFIED: gpt-4o-mini emits no reasoning blocks, so opencode's
    # thinking chrome was never captured. Left empty rather than guessed —
    # "thinking" as a substring collides with the "/thinking Expand thinking"
    # slash-menu row.
    thinking_markers=[],

    # Two plain spaces follow the ┃ gutter; no non-breaking space.
    prompt_continuation_chars=[" "],

    # No "↵ to send" hint exists in opencode's input box.
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,

    # Hook-detector-only field: it downgrades a stuck RUNNING to waiting_user
    # when the user has interrupted the turn from the keyboard.
    # Captured live in Phase 6 (v1.18.19): a double-Escape mid-generation
    # rewrites the assistant turn's footer pill from "▣  Build · GPT-4o mini"
    # to "▣  Build · GPT-4o mini · interrupted" and leaves it there. The model
    # name varies, so the stable part is the trailing "· interrupted"; the
    # busy hint ("esc again to interrupt") never contains it.
    interrupt_prompt_markers=["· interrupted"],

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

    # No analogue: opencode's bottom bar carries tokens/cost, never counts of
    # background bashes, subagents, monitors, or an auto-accept toggle.
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)


class OpencodeBackend:
    """opencode CLI adapter (verified against v1.18.19)."""

    name = "opencode"
    display_name = "opencode"
    binary = "opencode"
    version_args = ("--version",)
    install_hint = (
        "opencode CLI is required but not found. "
        "Install it from: https://opencode.ai/docs/"
    )
    # opencode ships a compiled Bun binary; the Homebrew/npm `opencode`
    # shim symlinks to `opencode.exe`, and argv[0] is whichever the user
    # invoked. Both basenames are matched so `overcode doctor` finds the
    # process either way.
    process_basenames = ("opencode", "opencode.exe")
    not_found_error = OpencodeNotFoundError
    # Verified against v1.18.19: `--session <id>` resumes and
    # `--session <id> --fork` branches into a new "(fork #1)" session.
    # HOOK_EVENTS comes from the bundled telemetry plugin, TRANSCRIPT_STATS
    # from the SQLite session store.
    # Deliberately absent: SESSION_ID_PRESCRIPTION (opencode mints its own
    # `ses_…` ids), PERMISSION_INJECTION (no per-launch allowlist flag
    # exists), SKILLS / SANDBOX_PROBE / SUBSCRIPTION_USAGE / AGENT_TEAMS.
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.FORK
        | BackendCapability.HOOK_EVENTS
        | BackendCapability.TRANSCRIPT_STATS
    )

    def executable(self) -> str:
        """The binary to invoke, honouring the OPENCODE_COMMAND override.

        Mirrors CLAUDE_COMMAND: the override is how the e2e mock harness
        substitutes a fake TUI.
        """
        return os.environ.get("OPENCODE_COMMAND", self.binary)

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        args = ["--session", session_id]
        if fork:
            args.append("--fork")
        return args

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """Construct the opencode CLI argument list.

        Flag mapping (Appendix A of the design doc, re-verified at v1.18.19):
          model              -> ``--model <provider/model>``
          bypass/permissive  -> ``--auto`` (deny rules still win; opencode
                                has no separate "ask nothing but obey deny"
                                mode, so both overcode modes collapse onto it)
          persona            -> ``--agent <name>``
          resume             -> ``--session <id>``
          fork               -> ``--session <id> --fork``

        ``allowed_tools`` has no v1.18.19 analogue — the researched
        ``--permissions`` flag does not exist — so it is ignored here and
        surfaced as an unsupported knob in ``docs/backends.md``.
        """
        cmd = [self.executable()]

        if spec.resume_session_id:
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))

        # opencode expects the fully-qualified `provider/model` form
        # (e.g. `openai/gpt-4o-mini`, `anthropic/claude-sonnet-4-5`).
        # Passed through as given so a bare model name fails loudly rather
        # than being silently mis-qualified.
        if spec.model:
            cmd.extend(["--model", spec.model])

        if spec.agent:
            cmd.extend(["--agent", spec.agent])

        if (
            spec.dangerously_skip_permissions
            or spec.skip_permissions
            or spec.permissiveness_mode in ("bypass", "permissive")
        ):
            cmd.append("--auto")

        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))

        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        """Put the telemetry plugin where the launched process will find it.

        Runs on every launch/restart/revive/fork so an upgraded overcode
        refreshes a stale copy. Failure is silent by design — a missing
        plugin costs hooks-grade status, not the launch, and the detection
        dispatcher falls back to pane polling on its own.
        """
        ensure_plugin_installed(spec.start_directory)

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        """opencode reads provider credentials from the ambient environment.

        The one thing that must be forwarded unconditionally is
        ``OVERCODE_STATE_DIR``: the plugin runs inside the opencode process
        and has to write hook state where this overcode instance reads it.
        ``OVERCODE_SESSION_NAME`` and ``OVERCODE_TMUX_SESSION`` are already in
        the launcher's shared prefix.

        In **bypass** mode only (never permissive — see
        ``docs/backends.md``'s "Permission modes" section), also sets
        ``OPENCODE_PERMISSION`` to an allow-everything JSON blob. Both
        overcode modes still pass ``--auto`` on the command line (opencode
        has no second, stronger flag the way Claude/codex/grok do), but
        `--auto` alone leaves the project's own ``"deny"`` rules in force —
        closer to Claude's `dontAsk` than to
        `--dangerously-skip-permissions`. ``OPENCODE_PERMISSION`` is merged
        into opencode's resolved config *after* project config (live-verified
        via `opencode debug config`, Ancillary section of the design doc), so
        it genuinely overrides deny rules a `--auto`-only launch could not —
        no file written, nothing to clean up, gone the moment the process
        that set it exits.
        """
        env: Dict[str, str] = {}
        state_dir = os.environ.get("OVERCODE_STATE_DIR")
        if state_dir:
            env["OVERCODE_STATE_DIR"] = shlex.quote(state_dir)

        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            env["OPENCODE_PERMISSION"] = shlex.quote(
                json.dumps(OPENCODE_ALLOW_EVERYTHING_PERMISSION)
            )

        return env

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Ctrl-C kills opencode outright (verified), so the interrupt step
        # uses Escape instead: the first press arms the interrupt, the
        # second cancels an in-flight turn. `/exit` then closes the app
        # cleanly and prints the resume hint.
        return [
            KeyPress("Escape", enter=False, delay_after=0.3),
            KeyPress("Escape", enter=False, delay_after=0.5),
            KeyPress("/exit", enter=True),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        # "/new  New session" — verified to reset the pane to the banner.
        return [KeyPress("/new", enter=True)]

    def approve_keys(self) -> List[KeyPress]:
        # "Allow once" is preselected; Enter confirms it.
        return [KeyPress("", enter=True)]

    def reject_keys(self) -> List[KeyPress]:
        # Escape dismisses the permission dialog and abandons the tool call.
        return [KeyPress("Escape", enter=False)]

    def startup_dialog_rules(self) -> List[DialogRule]:
        # None observed: with a provider credential in the environment,
        # opencode launches straight to the input box — no trust-folder
        # prompt, no provider picker, no onboarding.
        return []

    def prompt_ready_chars(self) -> Set[str]:
        # The input box gutter, drawn as soon as the TUI is interactive.
        return {"┃"}

    def status_patterns(self) -> StatusPatterns:
        return OPENCODE_PATTERNS

    def make_stats_reader(self) -> "StatsReader":
        from .opencode_stats import OpencodeStatsReader
        return OpencodeStatsReader()

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        """A live opencode process is all argv alone can tell us.

        Unlike Claude Code there is no ``--settings`` payload on the command
        line to inspect — telemetry is injected through a file in the
        project's ``.opencode/plugins/``, which argv never mentions. The
        per-agent plugin check lives in ``doctor`` where the session's
        ``start_directory`` is in hand.
        """
        from ..doctor import VERDICT_OK
        return VERDICT_OK, "opencode process running"

    def refine_health_verdict(
        self, session, verdict: str, details: str
    ) -> Tuple[str, str]:
        """Second pass with the session in hand: is the telemetry plugin there?

        This is opencode's equivalent of Claude Code's ``--settings`` check.
        Without the plugin the agent still runs, but its status comes from
        pane scraping rather than hook events, which is worth flagging.
        """
        from ..doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK

        if verdict != VERDICT_OK:
            return verdict, details
        start_directory = getattr(session, "start_directory", None)
        if not start_directory:
            return verdict, details
        if plugin_installed(start_directory):
            return VERDICT_OK, "opencode process running, telemetry plugin installed"
        return VERDICT_MISSING_SETTINGS, (
            "opencode running without overcode's telemetry plugin in "
            f"{Path(start_directory).joinpath(*PLUGIN_DIR_PARTS)} — status falls "
            "back to pane polling. Relaunch via `overcode restart` to install it."
        )

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


_backend: Optional[OpencodeBackend] = None


def get_opencode_backend() -> OpencodeBackend:
    """Module-level singleton — backends are stateless."""
    global _backend
    if _backend is None:
        _backend = OpencodeBackend()
    return _backend


def installed_version() -> Optional[str]:
    """Run `opencode --version`, returning the trimmed output or None.

    Always probes the real binary, never OPENCODE_COMMAND — a doctor check
    against the mock harness would be meaningless.
    """
    from ..dependency_check import check_agent_cli

    available, _path, version = check_agent_cli(get_opencode_backend())
    if not available or not version:
        return None
    return version.strip() or None


def version_findings(version: Optional[str] = None) -> List[str]:
    """Doctor warnings about the installed opencode, newest concern first.

    Returns human-readable strings (empty when everything looks fine) so the
    CLI can print them without importing version-comparison logic.
    """
    findings: List[str] = []

    resolved = version if version is not None else installed_version()
    if resolved is None:
        findings.append(
            "could not determine the installed opencode version "
            "(`opencode --version` failed) — overcode is tested against "
            f"{TESTED_OPENCODE_MIN} <= version < {TESTED_OPENCODE_MAX}"
        )
    else:
        in_range = version_in_tested_range(resolved)
        if in_range is None:
            findings.append(
                f"unrecognised opencode version string '{resolved}' — overcode "
                f"is tested against {TESTED_OPENCODE_MIN} <= version < "
                f"{TESTED_OPENCODE_MAX}"
            )
        elif not in_range:
            findings.append(
                f"opencode {resolved} is outside the tested range "
                f"{TESTED_OPENCODE_MIN} <= version < {TESTED_OPENCODE_MAX} — "
                "status detection reads the TUI's on-screen chrome and may drift"
            )

    if autoupdate_enabled():
        path = global_config_path()
        findings.append(
            f"opencode autoupdate is enabled in {path} — opencode ships every "
            "few days and its pane chrome is overcode's status signal; "
            'consider setting "autoupdate": false'
        )

    # Schema drift in the SQLite store is the other way an opencode upgrade
    # silently blanks a column, so it rides the same doctor pass.
    try:
        from .opencode_stats import schema_findings
        findings.extend(schema_findings())
    except Exception:
        pass

    return findings


__all__ = [
    "OPENCODE_ALLOW_EVERYTHING_PERMISSION",
    "OPENCODE_GLOBAL_CONFIG_NAMES",
    "OPENCODE_PATTERNS",
    "PLUGIN_DIR_PARTS",
    "PLUGIN_FILENAME",
    "PLUGIN_MARKER",
    "OpencodeBackend",
    "OpencodeNotFoundError",
    "OpencodeStatusPatterns",
    "TESTED_OPENCODE_MAX",
    "TESTED_OPENCODE_MIN",
    "TESTED_OPENCODE_RANGE",
    "autoupdate_enabled",
    "bundled_plugin_path",
    "ensure_plugin_installed",
    "get_opencode_backend",
    "global_config_path",
    "installed_version",
    "parse_version",
    "plugin_installed",
    "project_plugin_path",
    "version_findings",
    "version_in_tested_range",
]
