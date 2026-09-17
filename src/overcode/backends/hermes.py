"""hermes backend — launch, plugin telemetry and stats for the Hermes agent.

Hermes (https://github.com/NousResearch/hermes-agent) is overcode's fifth
backend, on the seam ``docs/design/agent-agnostic-backends-opencode.md``
§2.1 defines. Everything asserted here was verified live against Hermes
Agent v0.21.3 (2026.9.14, macOS/arm64, ``openai-api`` provider) on
2026-09-17; the pane corpus lives in ``tests/fixtures_hermes_panes/`` and is
replayed by ``tests/unit/test_status_detector_hermes.py``. The design write-
up is ``docs/design/agent-backend-hermes.md``.

Telemetry rides on a bundled Hermes *plugin* (``src/overcode/hermes_plugin/``),
installed globally into ``$HERMES_HOME/plugins/overcode/`` and enabled once
via ``hermes plugins enable overcode --no-allow-tool-override`` — the
opencode shape (a plugin that translates the CLI's own hook vocabulary into
Claude's and pipes it to ``overcode hook-handler``), not the grok shape (a
shell-hooks file whose dialect ``hook_handler`` has to learn). Stats come
from Hermes's SQLite store (``$HERMES_HOME/state.db``, ``HermesStatsReader``).
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
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


def _resolve_overcode_bin() -> str:
    """Resolve the absolute path to the overcode binary.

    Byte-identical to claude_code.py's / codex.py's helper (see the note
    there on why it is copied rather than imported): the plugin's hook
    subprocess has to find overcode from inside Hermes's own venv, where a
    bare ``overcode`` may not be on PATH.
    """
    which = shutil.which("overcode")
    if which:
        return which
    return f"{sys.executable} -m overcode.cli"


class HermesNotFoundError(AgentCliNotFoundError):
    """Raised when the hermes CLI isn't on PATH."""


# Hermes ships from `main` with a rolling version (0.21.3 at verification);
# `overcode doctor` warns when the installed version leaves the range these
# patterns and flags were verified against. 0.22 is treated as unverified
# until re-checked — the classic-CLI chrome moved several times in the
# 0.2x line (its own docs say the TUI is the recommended surface).
TESTED_HERMES_MIN = "0.21.0"
TESTED_HERMES_MAX = "0.22.0"             # exclusive upper bound
TESTED_HERMES_RANGE = (TESTED_HERMES_MIN, TESTED_HERMES_MAX)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# A regex that can never match, for Claude/opencode concepts Hermes has no
# analogue for.
_NEVER = r"(?!)"
_NEVER_SUBSTRING = "\x00"

# The plugin's on-disk identity. Both bundled files carry this marker so a
# user-authored plugin dir of the same name is never overwritten.
PLUGIN_MARKER = "OVERCODE-PLUGIN-MARKER: overcode-hermes-telemetry"
PLUGIN_NAME = "overcode"
PLUGIN_FILES = ("plugin.yaml", "__init__.py")

# The prompt glyph. Hermes never draws it bare: an empty input shows a
# rotating placeholder hint ("❯ Draft a reply to the last email in my
# inbox"), a busy turn swaps the whole line for "☤ ❯ msg=interrupt · /queue
# · /bg · /steer · Ctrl+C cancel", and an open dialog shows "⚠ ❯".
PROMPT_GLYPH = "❯"


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Extract (major, minor, patch) from `hermes --version` output.

    The first line reads ``Hermes Agent v0.21.3 (2026.9.14) · upstream
    98f758ae`` — the first version-shaped token is the one we want (the
    date in parentheses is not x.y.z-shaped, so it can't be mistaken).
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_in_tested_range(version: str) -> Optional[bool]:
    """True/False when `version` is inside TESTED_HERMES_RANGE, None if unparseable."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    low = parse_version(TESTED_HERMES_MIN)
    high = parse_version(TESTED_HERMES_MAX)
    return low <= parsed < high


# ── on-disk locations ──────────────────────────────────────────────────────

def hermes_home() -> Path:
    """Hermes's data directory: ``$HERMES_HOME`` or ``~/.hermes``.

    Hermes's own resolver (``hermes_constants.get_hermes_home``) honours
    HERMES_HOME first — including the per-profile homes ``--profile``
    writes into it — so overcode reads the same variable rather than
    hard-coding ``~/.hermes``.
    """
    override = os.environ.get("HERMES_HOME")
    return Path(override) if override else Path.home() / ".hermes"


def config_path() -> Path:
    return hermes_home() / "config.yaml"


def state_db_path() -> Path:
    return hermes_home() / "state.db"


def bundled_plugin_dir() -> Path:
    """The plugin shipped inside the overcode package."""
    return Path(__file__).parent.parent / "hermes_plugin"


def plugin_dir() -> Path:
    """Where the plugin has to live for Hermes to discover it."""
    return hermes_home() / "plugins" / PLUGIN_NAME


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def plugin_installed() -> bool:
    """True when ``$HERMES_HOME/plugins/overcode/`` carries our marker."""
    text = _read(plugin_dir() / "plugin.yaml")
    return bool(text) and PLUGIN_MARKER in text


def ensure_plugin_installed() -> Optional[Path]:
    """Copy the bundled plugin into ``$HERMES_HOME/plugins/overcode/``.

    Global rather than project-scoped because Hermes only discovers plugins
    under its home (``hermes_cli/plugins_discovery.py``) — there is no
    per-project plugin directory the way opencode has ``.opencode/plugins``.
    Harmless globally: the plugin no-ops without the ``OVERCODE_*`` env vars.

    Same idempotent, non-destructive contract as ``opencode.
    ensure_plugin_installed``/``grok.ensure_hooks_installed``:

    * missing            → written
    * present, ours      → rewritten when the bundled copy has moved on
    * present, ours, same→ left alone
    * present, not ours  → left alone (a user-authored plugin of that name)

    Returns the plugin directory, or None when nothing could be installed.
    """
    source_dir = bundled_plugin_dir()
    target_dir = plugin_dir()

    existing_manifest = _read(target_dir / "plugin.yaml")
    if existing_manifest is not None and PLUGIN_MARKER not in existing_manifest:
        return None

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in PLUGIN_FILES:
            content = _read(source_dir / name)
            if content is None:
                return None
            target = target_dir / name
            if _read(target) == content:
                continue
            tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, target)
    except OSError:
        return None
    return target_dir


def _load_config() -> Optional[dict]:
    text = _read(config_path())
    if not text:
        return None
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def plugin_enabled(config: Optional[dict] = None) -> Optional[bool]:
    """Whether ``plugins.enabled`` in Hermes's config.yaml lists the plugin.

    Hermes plugins are opt-in (``hermes_cli/plugins_discovery.py``:
    ``gate_manifest`` skips any user plugin not in the allow-list, and
    ``plugins.disabled`` wins over it). None when the config is unreadable.
    """
    if config is None:
        config = _load_config()
    if config is None:
        return None
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return False
    disabled = plugins.get("disabled")
    if isinstance(disabled, list) and PLUGIN_NAME in disabled:
        return False
    enabled = plugins.get("enabled")
    return isinstance(enabled, list) and PLUGIN_NAME in enabled


def ensure_plugin_enabled(executable: str) -> bool:
    """Add the plugin to ``plugins.enabled`` through Hermes's own CLI.

    ``hermes plugins enable overcode --no-allow-tool-override`` is the one
    non-interactive spelling (bare ``enable`` prompts on /dev/tty about the
    tools.override capability, which this plugin never requests). Hermes
    rewrites config.yaml itself, so overcode never has to round-trip the
    user's 100KB commented config through a YAML library. Only runs when
    the allow-list doesn't already carry the plugin — one ~2s Hermes boot
    per install, never per launch.
    """
    if plugin_enabled():
        return True
    try:
        completed = subprocess.run(
            shlex.split(executable) + [
                "plugins", "enable", PLUGIN_NAME, "--no-allow-tool-override",
            ],
            capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return completed.returncode == 0


def remove_plugin() -> Tuple[bool, str]:
    """``overcode hooks uninstall-backend hermes``: delete our plugin dir.

    Only removes a directory carrying the marker; a user-authored plugin of
    the same name is left alone. The ``plugins.enabled`` entry is left in
    config.yaml — Hermes tolerates a listed-but-missing plugin, and
    ``hermes plugins disable overcode`` is the user's own tool for that.
    """
    target_dir = plugin_dir()
    manifest = _read(target_dir / "plugin.yaml")
    if manifest is None:
        return True, f"No hermes plugin found at {target_dir}"
    if PLUGIN_MARKER not in manifest:
        return False, f"{target_dir} exists but is not overcode-managed — leaving it alone"
    try:
        shutil.rmtree(target_dir)
    except OSError as exc:
        return False, f"could not remove {target_dir}: {exc}"
    return True, f"Removed {target_dir}"


def configured_context_length(config: Optional[dict] = None) -> Optional[int]:
    """``model.context_length`` from Hermes's config.yaml, when set.

    Hermes divides its own CTX% by this when the user sets it (otherwise by
    the provider's reported/catalogued window) — honouring it keeps the CTX
    column in step with the agent's status bar (#469).
    """
    if config is None:
        config = _load_config()
    if config is None:
        return None
    model = config.get("model")
    if not isinstance(model, dict):
        return None
    value = model.get("context_length")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


# ── status patterns ───────────────────────────────────────────────────────

class HermesStatusPatterns(StatusPatterns):
    """Hermes's classic-CLI chrome, with both pane-bottom predicates widened.

    The prompt_toolkit REPL keeps two chrome lines *below* the input line
    (a rule and, above the input, the ``☤`` status bar), and the input line
    itself never carries a bare glyph — so the base class's default-tail
    2/4 windows and exact-glyph match both miss. Same widening codex and
    opencode needed, bounded to the 10-line window ``detect_status`` hands
    these predicates.
    """

    def is_busy(self, lines: List[str], tail: int = 10) -> bool:
        return super().is_busy(lines, tail=tail)

    def is_input_ready(self, lines: List[str], tail: int = 10) -> bool:
        return any(self.is_ready_prompt_line(line) for line in lines[-tail:])

    @staticmethod
    def is_ready_prompt_line(line: str) -> bool:
        """True for the live input line — ``❯`` alone or ``❯ <placeholder or
        typed text>``. The busy (``☤ ❯ …``) and dialog (``⚠ ❯``) variants
        carry a leading glyph and never match; so do the ``│ ❯ 1. Allow
        once`` selector rows inside a boxed dialog.
        """
        stripped = line.strip()
        return stripped == PROMPT_GLYPH or stripped.startswith(f"{PROMPT_GLYPH} ")


HERMES_PATTERNS = HermesStatusPatterns(
    # ── Permission dialog ────────────────────────────────────────────────
    # From permission_required.txt (approvals.mode: manual):
    #   ╭────────────────────────────────────────────────╮
    #   │ ⚠️  Dangerous Command                          │
    #   │ rm -rf ./junkdir_a                             │
    #   │ ❯ 1. Allow once                                │
    #   │   2. Allow for this session                    │
    #   │   3. Add to permanent allowlist                │
    #   │   4. Deny                                      │
    #   │ recursive delete                               │
    #   ╰────────────────────────────────────────────────╯
    #     ↑/↓ to select, Enter to confirm  (298s)
    #   ⚠ ❯
    # "Enter to confirm" also anchors the /new confirmation box
    # (new_confirm.txt: "Type 1/2/3 or use ↑/↓ then Enter") — a human has
    # to answer that one too, so waiting_user is the right answer for both.
    permission_patterns=[
        "dangerous command",
        "allow once",
        "enter to confirm",
    ],

    # ── Busy ─────────────────────────────────────────────────────────────
    # busy.txt: the input line becomes
    #   ☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel
    # for the whole turn, with a kaomoji spinner ("(◔_◔) formulating...",
    # "(｡•́︿•̀｡) pondering...", verbs rotate) above the status bar. The
    # prompt-line hint is the stable signal; the spinner text is not.
    active_indicators=[
        "ctrl+c cancel",
    ],

    # Tool lines ("┊ 💻 $  sleep 6 + 1 command  0.6s") are past-tense once
    # the call returns and stay on screen, so — like codex/opencode —
    # tool-execution detection is disabled; the busy hint covers in-flight.
    execution_indicators=[],

    waiting_patterns=[
        "do you want",
        "proceed",
        "yes/no",
        "[y/n]",
        "press any key",
    ],

    prompt_chars=[PROMPT_GLYPH],
    line_prefixes=[f"{PROMPT_GLYPH} ", "● ", "┊ ", "◆ ", "◈ "],

    # The status bar (" ☤ gpt-5-mini │ ~12.7K/400K │ [░░░░░░░░░░] ~3% │ … │
    # 12s │ ⏲ 8s │ ✓ 0s") ticks every second even while idle, and the
    # busy prompt line shares the ☤ prefix — both are filtered from the
    # content hash so an idle agent never reads as "content changing".
    status_bar_prefixes=["☤ "],

    # command_menu.txt: "/new    Start a new session (fresh session ID +
    # history) (usage: /new [name])" — the default two-space-gap pattern
    # matches Hermes's menu rows as-is.
    command_menu_pattern=r"^\s*/[\w-]+\s{2,}\S",

    spawn_failure_patterns=[
        "command not found",
        "not found:",
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",
    ],

    # No plan-mode / "approve this plan" stage.
    approval_patterns=[_NEVER],

    daemon_active_indicators=["ctrl+c cancel"],
    daemon_tool_indicators=["┊ ", "● "],

    # A provider failure ("OpenAI API rejected your API key … Provider
    # said: HTTP 401") settles straight back at the prompt in the same
    # frame, exactly codex's error_bad_model.txt shape — the corpus README
    # documents waiting_user, not error, as ground truth. Left empty for
    # the same reason codex's is.
    error_patterns=[],

    permission_chrome_markers=[
        "dangerous command",
        "allow once",
        "allow for this session",
        "add to permanent allowlist",
        "enter to confirm",
        "to select",
    ],

    # "● <prompt>" is the user's echoed turn; "┊ 💻 …" a tool line; the
    # boxed "╭─ ☤ Hermes ─" panel holds the reply. ┊ marks agent output.
    tool_output_prefixes=["┊ ", "◆ "],
    tool_output_marker="┊",

    busy_markers=["ctrl+c cancel"],

    # The context bar segment of the status line — present in every live
    # frame ("[░░░░░░░░░░] 0%" / "[░░░░░░░░░░] --"), absent from the
    # scrollback after /quit (exited_shell.txt). What _is_shell_prompt
    # checks to avoid reporting a live REPL as an exited shell.
    input_hint_markers=["[░"],

    # The kaomoji spinner's verb rotates; these are the ones captured.
    thinking_markers=[
        "pondering", "contemplating", "reflecting", "formulating",
        "mulling", "reasoning", "analyzing",
    ],

    prompt_continuation_chars=[" "],

    # No "↵ to send" style hint in Hermes's input box.
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,

    # interrupted.txt / interrupted_api.txt: a single C-c mid-turn prints
    # "⚡ Interrupted during API call." and "Operation interrupted: waiting
    # for model response (2.8s elapsed)." and returns to the prompt; the
    # plugin's Interrupt event covers the hooks path, this the polling one.
    interrupt_prompt_markers=["Operation interrupted", "Interrupted during API call"],

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

    # No analogue: Hermes's status bar carries model/context/cost/timers,
    # never counts of background bashes, subagents, monitors, or an
    # auto-accept toggle (its "⚙ 1" is the tool-call counter).
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)


class HermesBackend:
    """Hermes agent adapter (verified against v0.21.3).

    RESUME (``--resume <id>``), HOOK_EVENTS (the bundled plugin) and
    TRANSCRIPT_STATS (``state.db``). No FORK: Hermes branches only via the
    in-session ``/branch`` command, with no launch flag. No
    SESSION_ID_PRESCRIPTION: ids are minted by Hermes (``YYYYMMDD_HHMMSS_
    <hex6>``) and learned through the plugin's SessionStart. No
    PERMISSION_INJECTION: the only approval knobs are ``--yolo`` and the
    ``approvals`` config section, no per-launch allowlist flag. No SKILLS /
    SANDBOX_PROBE / SUBSCRIPTION_USAGE / AGENT_TEAMS.
    """

    name = "hermes"
    display_name = "hermes"
    binary = "hermes"
    version_args = ("--version",)
    install_hint = (
        "Hermes agent is required but not found. "
        "Install it with: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    )
    # ``hermes`` on PATH is a bash shim that execs Hermes's own venv python:
    #   /Users/x/.hermes/hermes-agent/venv/bin/python /Users/x/.hermes/hermes-agent/hermes --cli
    # so the process the pane actually runs has basename ``python`` — far too
    # broad to match on. ``process_argv_markers`` (below) is the seam that
    # identifies it by argv instead; the basename tuple is kept for the
    # shim's own brief lifetime and for installs that put a real binary on
    # PATH.
    process_basenames = ("hermes",)
    process_argv_markers = ("hermes-agent/hermes",)
    not_found_error = HermesNotFoundError
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.HOOK_EVENTS
        | BackendCapability.TRANSCRIPT_STATS
    )
    fork_prescribes_new_session_id = False

    def executable(self) -> str:
        """The binary to invoke, honouring the HERMES_COMMAND override.

        Mirrors CLAUDE_COMMAND/OPENCODE_COMMAND/CODEX_COMMAND/GROK_COMMAND:
        the override is how the e2e mock harness substitutes a fake CLI.
        """
        return os.environ.get("HERMES_COMMAND", self.binary)

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        # No fork grammar exists; the launcher never asks (FORK undeclared),
        # and a resume is the closest thing if it ever does.
        return ["--resume", session_id]

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """Construct the hermes CLI argument list.

        Flag mapping (verified at v0.21.3, ``hermes chat --help``):
          fresh              -> ``hermes --cli [opts]``
          resume             -> ``hermes --cli --resume <id> [opts]``
          model              -> ``-m <model>`` (Hermes's own id form: a bare
                                ``gpt-5-mini`` for openai-api, or
                                ``provider/model`` for routed providers)
          bypass             -> ``--yolo`` (skips every dangerous-command
                                approval prompt)
          permissive, normal -> no flag: Hermes has no per-launch approval
                                mode below ``--yolo``; ``approvals.mode`` in
                                its config.yaml (``smart`` by default — an
                                auxiliary model pre-screens and only escalates
                                to a human prompt) is the user's knob.

        ``--cli`` is passed on every launch: Hermes's ``display.interface``
        config can default a bare ``hermes`` to the Node-based TUI, whose
        chrome this backend has not been verified against. Explicit beats
        config, the same posture grok takes with ``--permission-mode``.

        ``allowed_tools`` and ``agent`` (persona) have no analogue — no
        allowlist flag exists (``--toolsets`` is a different axis), and
        ``--profile`` selects a whole HERMES_HOME, not a persona — so both
        are silently ignored, as for opencode/codex. ``prescribed_session_id``
        is ignored too: no ``--session-id``-shaped flag exists.

        ``--accept-hooks`` is not needed: the plugin route has no shell-hook
        consent step.
        """
        cmd = [self.executable(), "--cli"]

        if spec.resume_session_id:
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))

        if spec.model:
            cmd.extend(["-m", spec.model])

        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            cmd.append("--yolo")

        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))

        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        """Install/refresh the plugin and make sure Hermes will load it.

        Runs on every launch/restart/revive so an upgraded overcode
        refreshes a stale copy — same posture as ``opencode.
        ensure_plugin_installed``. Failure is silent by design: a missing
        plugin costs hooks-grade status, not the launch, and the detection
        dispatcher falls back to pane polling on its own.

        Skipped entirely when ``backend_telemetry.hermes`` is off in
        overcode's config.yaml (``config.get_backend_telemetry_enabled``).
        """
        from ..config import get_backend_telemetry_enabled
        if not get_backend_telemetry_enabled(self.name):
            return None
        if ensure_plugin_installed() is None:
            return None
        ensure_plugin_enabled(self.executable())
        return None

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        """Hermes reads its provider credentials (``$HERMES_HOME/.env``) ambiently.

        Two things are forwarded: ``OVERCODE_HOOK_COMMAND`` tells the
        in-process plugin exactly how to invoke ``overcode hook-handler``
        (Hermes's venv python has no ``overcode`` on its PATH by default —
        the same "how does the hook subprocess find overcode" answer
        Claude's ``--settings`` injection bakes in), and
        ``OVERCODE_STATE_DIR`` (test isolation only) so that subprocess
        writes hook state where this overcode instance reads it. Everything
        else the plugin needs (``OVERCODE_SESSION_NAME``/``OVERCODE_TMUX_
        SESSION``) is already in the launcher's shared prefix.
        """
        env = {"OVERCODE_HOOK_COMMAND": shlex.quote(_resolve_overcode_bin())}
        state_dir = os.environ.get("OVERCODE_STATE_DIR")
        if state_dir:
            env["OVERCODE_STATE_DIR"] = shlex.quote(state_dir)
        return env

    def graceful_exit_keys(self) -> List[KeyPress]:
        # A single C-c is safe on Hermes (interrupts the turn; "double-press
        # within 2s to force exit" is its own documented escape hatch, so
        # never send two). /quit exits cleanly and prints the
        # `hermes --resume <id>` hint (exited_shell.txt). Verified live:
        # C-c on an idle prompt leaves the process alive; /quit mid-turn
        # exits immediately.
        return [
            KeyPress("C-c", enter=False, delay_after=0.5),
            KeyPress("/quit", enter=True),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        # /new opens a confirmation box ("⚠️ /new — destroys conversation
        # state", options "[1] Approve Once / [2] Always Approve / [3]
        # Cancel", new_confirm.txt) under the default
        # ``tools.slash_confirm.destructive_slash_confirm: true`` —
        # option 1 is the one-time confirm. Sending "1" when the box has
        # been disabled in config would land in the input line as a stray
        # "1"; the extra keypress is the lesser evil vs. a /new that never
        # completes.
        return [
            KeyPress("/new", enter=True, delay_after=1.0),
            KeyPress("1", enter=True),
        ]

    def approve_keys(self) -> List[KeyPress]:
        # permission_required.txt: "❯ 1. Allow once" is pre-selected but
        # a bare Enter is *not* enough on its own reliably — the dialog
        # says "↑/↓ to select, Enter to confirm" and typing the digit
        # then Enter is what was verified live to run the command.
        return [KeyPress("1", enter=True)]

    def reject_keys(self) -> List[KeyPress]:
        # "4. Deny" — verified live: "[You denied this command — it did not
        # run.]" then the agent continues its turn. Escape is documented as
        # cancel too, but the digit is unambiguous even when the option
        # order changes (the number is read off the dialog in the corpus).
        return [KeyPress("4", enter=True)]

    def startup_dialog_rules(self) -> List[DialogRule]:
        # None observed: idle_fresh.txt (a plain `hermes --cli` in a
        # never-before-visited git-initialised scratch dir) shows the
        # banner, "Welcome to Hermes Agent!" and the prompt with no trust
        # or telemetry dialog. First-run *setup* (no provider configured)
        # is an interactive wizard overcode can't drive — `overcode doctor`
        # flags a missing provider instead.
        return []

    def prompt_ready_chars(self) -> Set[str]:
        # Never matches exactly — Hermes never draws a bare glyph (see
        # PROMPT_GLYPH). The launcher consults ``prompt_ready_line`` below
        # first; this set is the documented fallback shape.
        return {PROMPT_GLYPH}

    def prompt_ready_line(self, line: str) -> bool:
        """The launcher's "is the input prompt up yet?" predicate.

        Exact-set matching (``prompt_ready_chars``) can't express "the
        glyph followed by a rotating placeholder", which is the only idle
        shape Hermes ever draws.
        """
        return HermesStatusPatterns.is_ready_prompt_line(line)

    def status_patterns(self) -> StatusPatterns:
        return HERMES_PATTERNS

    def make_stats_reader(self) -> "StatsReader":
        from .hermes_stats import HermesStatsReader
        return HermesStatsReader()

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        """A live hermes process is all argv alone can tell us.

        Telemetry rides on the plugin in ``$HERMES_HOME/plugins/``, which
        argv never mentions — the per-install check is ``refine_health_
        verdict`` below (opencode's shape).
        """
        from ..doctor import VERDICT_OK
        return VERDICT_OK, "hermes process running"

    def refine_health_verdict(
        self, session, verdict: str, details: str
    ) -> Tuple[str, str]:
        """Second pass: is the plugin installed *and* enabled?

        Both halves matter — Hermes silently skips an installed plugin
        that isn't in ``plugins.enabled``.
        """
        from ..doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK

        if verdict != VERDICT_OK:
            return verdict, details
        if not plugin_installed():
            return VERDICT_MISSING_SETTINGS, (
                f"hermes running without overcode's telemetry plugin in "
                f"{plugin_dir()} — status falls back to pane polling. "
                "Relaunch via `overcode restart` to install it."
            )
        if plugin_enabled() is False:
            return VERDICT_MISSING_SETTINGS, (
                "hermes has overcode's telemetry plugin on disk but "
                "`plugins.enabled` in its config.yaml does not list it — "
                "run `hermes plugins enable overcode --no-allow-tool-override` "
                "or relaunch via `overcode restart`."
            )
        return VERDICT_OK, "hermes process running, telemetry plugin installed and enabled"

    def uninstall_telemetry(self, project_dir: Optional[str] = None) -> Tuple[bool, str]:
        """``overcode hooks uninstall-backend hermes`` (see cli/hooks.py)."""
        return remove_plugin()

    def doctor_findings(self) -> List[str]:
        """Fleet-level warnings for ``overcode doctor`` (see cli/doctor.py)."""
        return version_findings()

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


_backend: Optional[HermesBackend] = None


def get_hermes_backend() -> HermesBackend:
    """Module-level singleton — backends are stateless."""
    global _backend
    if _backend is None:
        _backend = HermesBackend()
    return _backend


def installed_version() -> Optional[str]:
    """Run `hermes --version`, returning the trimmed first line or None.

    Always probes the real binary, never HERMES_COMMAND (respect_override=
    False) — a doctor check against the mock harness would be meaningless.
    """
    from ..dependency_check import check_agent_cli

    available, _path, version = check_agent_cli(
        get_hermes_backend(), respect_override=False
    )
    if not available or not version:
        return None
    first_line = version.strip().splitlines()[0] if version.strip() else ""
    return first_line or None


def provider_configured() -> Optional[bool]:
    """Whether Hermes's config.yaml names a model provider/default model.

    A fresh install with no provider runs its interactive setup wizard on
    launch — a dialog overcode cannot drive — so doctor names it up front.
    None when the config is unreadable (nothing to say).
    """
    config = _load_config()
    if config is None:
        return None
    model = config.get("model")
    if not isinstance(model, dict):
        return False
    return bool(model.get("default")) and bool(model.get("provider"))


def version_findings(version: Optional[str] = None) -> List[str]:
    """Doctor warnings about the installed hermes, newest concern first.

    Returns human-readable strings (empty when everything looks fine) so
    the CLI can print them without importing version-comparison logic.
    """
    findings: List[str] = []

    resolved = version if version is not None else installed_version()
    if resolved is None:
        findings.append(
            "could not determine the installed hermes version "
            "(`hermes --version` failed) — overcode is tested against "
            f"{TESTED_HERMES_MIN} <= version < {TESTED_HERMES_MAX}"
        )
        return findings

    in_range = version_in_tested_range(resolved)
    if in_range is None:
        findings.append(
            f"unrecognised hermes version string '{resolved}' — overcode "
            f"is tested against {TESTED_HERMES_MIN} <= version < "
            f"{TESTED_HERMES_MAX}"
        )
    elif not in_range:
        findings.append(
            f"hermes {resolved} is outside the tested range "
            f"{TESTED_HERMES_MIN} <= version < {TESTED_HERMES_MAX} — "
            "status detection reads the classic CLI's on-screen chrome and may drift"
        )

    if plugin_installed() and plugin_enabled() is False:
        findings.append(
            f"overcode's hermes telemetry plugin is installed at {plugin_dir()} "
            "but not listed in `plugins.enabled` in Hermes's config.yaml — "
            "hooks-grade status will not fire; run `hermes plugins enable "
            "overcode --no-allow-tool-override` or relaunch via `overcode restart`"
        )

    if provider_configured() is False:
        findings.append(
            f"hermes has no model provider configured in {config_path()} — "
            "a launch would open Hermes's interactive setup wizard, which "
            "overcode cannot drive; run `hermes model` (or `hermes config set "
            "model.provider <name>` + `model.default <model>`) once first"
        )

    # state.db schema drift is the other way a Hermes upgrade silently
    # blanks the stats columns, so it rides the same doctor pass (mirrors
    # opencode's SQLite schema_findings() call here).
    try:
        from .hermes_stats import schema_findings
        findings.extend(schema_findings())
    except Exception:
        pass

    return findings


__all__ = [
    "HERMES_PATTERNS",
    "HermesBackend",
    "HermesNotFoundError",
    "HermesStatusPatterns",
    "PLUGIN_MARKER",
    "PLUGIN_NAME",
    "PROMPT_GLYPH",
    "TESTED_HERMES_MAX",
    "TESTED_HERMES_MIN",
    "TESTED_HERMES_RANGE",
    "bundled_plugin_dir",
    "config_path",
    "configured_context_length",
    "ensure_plugin_enabled",
    "ensure_plugin_installed",
    "get_hermes_backend",
    "hermes_home",
    "installed_version",
    "parse_version",
    "plugin_dir",
    "plugin_enabled",
    "plugin_installed",
    "provider_configured",
    "remove_plugin",
    "state_db_path",
    "version_findings",
    "version_in_tested_range",
]
