"""opencode2 status patterns derived from the v2 pane corpus.

``OPENCODE2_PATTERNS`` is the ``StatusPatterns`` instance
``Opencode2Backend.status_patterns()`` returns. The patterns are tuned to
``tests/fixtures_opencode2_panes/`` — verbatim ``tmux capture-pane -p``
captures of real opencode2 v0.0.0-dev-19272 sessions (120-column panes,
Sep 16 2026), replayed by ``tests/unit/test_status_detector_opencode2.py``.

Provenance discipline, same as the v1 file: every field below cites the
corpus line that grounds it. Fields fall into three buckets:

* **corpus-proven** — the comment quotes the captured line;
* **carried from v1** — backend-independent lists (shell error texts,
  generic confirmation phrases) that no v2 capture contradicts; the comment
  says so explicitly;
* **dropped from v1** — v1 glyphs the v2 corpus shows are gone (v2 dropped
  the ``▣`` response pill and the ``✱`` tool-call asterisk entirely — the
  v2 corpus contains neither glyph anywhere).

The chrome the two corpora agree on is deliberately identical: ``┃``
input-box gutter, ``╹▀`` bottom border, ``ctrl+p commands`` footer hint,
``esc interrupt`` busy footer, ``⇆ select  enter confirm`` permission
chrome, and the ``· interrupted`` suffix on a keyboard-abandoned turn.
"""

from .opencode import OpencodeStatusPatterns

# A regex that can never match, for the Claude concepts opencode2 has no
# analogue for (plan-mode approval, subagent/monitor counts, auto-accept).
_NEVER = r"(?!)"
# Same idea for substring fields — NUL never appears in captured pane text.
_NEVER_SUBSTRING = "\x00"

# opencode2 is a rolling preview with no stable tags, so — unlike v1's
# TESTED_OPENCODE_RANGE — there is no version range these patterns are
# verified against, only the build the corpus was captured from.
OPENCODE2_CORPUS_BUILD = "v0.0.0-dev-19272"

OPENCODE2_PATTERNS = OpencodeStatusPatterns(
    # ── Permission dialog ────────────────────────────────────────────────
    # permission_required.txt:
    #   ┃  △ Permission required
    #   ┃  $ echo hi2
    #   ┃   Allow once   Always allow   Reject    ctrl+f fullscreen  ⇆ select  enter confirm
    # The load-bearing delta from v1: v2 renamed v1's "Allow always" to
    # "Always allow" — the old spelling must NOT be kept.
    permission_patterns=[
        "permission required",
        "allow once",
        "always allow",
        "enter confirm",
    ],

    # ── Busy ─────────────────────────────────────────────────────────────
    # busy.txt bottom bar, mid-generation:
    #   ⬝⬝■■■■■■ esc interrupt       shift+tab agents  ctrl+p commands
    # One Escape arms the interrupt and the hint becomes "esc again to
    # interrupt" (captured live in the same session, one press into the
    # interrupt sequence). Identical affordance to v1.
    active_indicators=[
        "esc interrupt",
        "esc again to interrupt",
    ],

    # v2 renders finished tool calls the same way v1 does —
    # idle_after_response.txt keeps "→ Skill "using-superpowers"" and
    # "+ Thought · 189ms" on screen after the turn ends. Matching them
    # would report a settled agent as running, so tool-execution detection
    # is deliberately disabled; "esc interrupt" already covers in-flight.
    execution_indicators=[],

    # Carried from v1: generic confirmation phrases for ad-hoc dialogs.
    # No v2 corpus pane contains any of them (verified by grep), and none
    # of the opencode2 confirmation UI observed so far goes beyond the
    # permission dialog, which phase 4 catches first.
    waiting_patterns=[
        "do you want",
        "proceed",
        "yes/no",
        "[y/n]",
        "press any key",
    ],

    # The input box gutter. A bare "┃" line is an empty input —
    # every live pane in the corpus draws it (idle_fresh.txt line 1: "┃").
    prompt_chars=["┃"],

    # Display-cleaning prefixes, corpus-proven: the box gutter ("┃  ",
    # "┃ "), the tool-call arrow (idle_after_response.txt: "→ Skill …")
    # and the thought pill ("+ Thought · 189ms"), plus the plain prose
    # list markers carried from v1. v1's "▣"/"✱" entries are dropped —
    # neither glyph appears anywhere in the v2 corpus.
    line_prefixes=["┃  ", "┃ ", "→ ", "+ ", "- ", "• "],

    # Only the input box's bottom border is unambiguous chrome. The info
    # bar below it starts with the project directory
    # (idle_after_response.txt: "/tmp/opencode/oc2cap-work/plain …") or the
    # MCP pill (idle_fresh.txt: "⊙ 0 MCP /mcps") — no stable prefix.
    status_bar_prefixes=["╹"],

    # command_menu.txt rows are drawn inside the box exactly like v1:
    #   ┃ /agents                   Switch agent                                       ┃
    #   ┃ /exit                     Exit the app                                       ┃
    command_menu_pattern=r"^\s*┃?\s*/[\w-]+\s{2,}\S",

    # Carried from v1: shell-level spawn errors, backend-independent.
    spawn_failure_patterns=[
        "command not found",
        "not found:",
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",
    ],

    # opencode2 has no plan-mode / "approve this plan" stage.
    approval_patterns=[_NEVER],

    # Supervisor-daemon fields (same caveat as v1: only meaningful if the
    # supervisor's own meta-agent ever stops being Claude). The busy
    # footer hints are corpus-proven (busy.txt); the tool glyphs are the
    # v2 corpus's — v1's "▣"/"✱" are gone, the v2 corpus uses "→"
    # (→ Skill), "+" (+ Thought) and "$" ($ echo hi2 inside the box).
    daemon_active_indicators=["esc interrupt", "esc again to interrupt"],
    daemon_tool_indicators=["→ ", "+ ", "$ "],

    # error_api_key.txt renders a provider failure as a plain transcript
    # line — no red box glyph survives ANSI stripping:
    #     Error: Provider request failed with HTTP 404
    #     Build · GPT-4o mini · 119ms
    # The first pattern is corpus-proven; the rest are carried from v1's
    # OpenAI-compatible provider error texts (same aisdk provider stack)
    # pending a v2 capture that proves them obsolete. Anything unmatched
    # degrades to waiting_user, which is honest ("stopped, needs you").
    error_patterns=[
        r"Error: Provider request failed with HTTP \d+",
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

    # permission_required.txt dialog footer:
    #   ctrl+f fullscreen  ⇆ select  enter confirm
    # Byte-identical to v1's permission chrome.
    permission_chrome_markers=[
        "enter confirm",
        "⇆ select",
        "ctrl+f fullscreen",
    ],

    # The v2 corpus contains no "▣" anywhere — assistant turns close with
    # a bare pill ("Build · acme-llm-1 · 1.3s · 93.0 tok/s"), and tool
    # work is headed by "→" (→ Skill), "+" (+ Thought) and "$" ($ echo
    # hi2). v1's ▣-based markers would never fire.
    tool_output_prefixes=["→ ", "+ ", "$ "],
    tool_output_marker="→",

    # busy.txt bottom bar (and the armed variant, captured live):
    #   ⬝⬝■■■■■■ esc interrupt          /  esc again to interrupt
    busy_markers=["esc interrupt", "esc again to interrupt"],

    # Present in every live opencode2 pane, gone once the TUI exits — used
    # to rule out a shell-prompt false positive. Fresh/idle footer
    # (idle_fresh.txt): "shift+tab agents  ctrl+p commands" — note v2's
    # agents key is shift+tab, v1's was tab. Settled footer
    # (idle_after_response.txt): "10.7K (2%) · $0.01  ctrl+p commands".
    # "╹▀" is the input box's bottom border, which survives even when a
    # narrow pane truncates the hints.
    input_hint_markers=["ctrl+p commands", "shift+tab agents", "╹▀"],

    # UNVERIFIED, kept empty as in v1: in-flight reasoning chrome was
    # observed live during capture (a "⠹ Thinking" line, kimi-k3) but
    # never in a settled corpus pane — and the busy footer ("esc
    # interrupt") is up during reasoning, so P9 already reports running.
    # Guessing a substring here risks matching the finished "+ Thought"
    # pills that stay on screen after the turn ends.
    thinking_markers=[],

    # Two plain spaces follow the ┃ gutter; no non-breaking space —
    # idle_fresh.txt line 22: "┃  Ask anything… "Fix broken tests"".
    prompt_continuation_chars=[" "],

    # No "↵ to send" hint exists in opencode2's input box (no corpus pane
    # shows one).
    autocomplete_hint_symbol=_NEVER_SUBSTRING,
    autocomplete_hint_word=_NEVER_SUBSTRING,

    # Hook-detector-only field: it downgrades a stuck RUNNING to
    # waiting_user when the user has interrupted the turn from the
    # keyboard. interrupted.txt, captured live with a double-Escape
    # mid-generation ("write a 300-line poem"):
    #     Build · acme-llm-1 · 3.9s · interrupted
    # Same trailing suffix as v1; the busy hint ("esc again to
    # interrupt") never contains it.
    interrupt_prompt_markers=["· interrupted"],

    # Kept from v1: only consulted by line_starts_with_any alongside
    # execution_indicators, which is empty (see above), so this shape is
    # inert — retained so the field cannot silently drift to Claude's.
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

    # No analogue: v2's bottom bar carries the project directory and
    # context/cost inline (idle_after_response.txt: "10.7K (2%) · $0.01")
    # — never counts of background bashes, subagents, monitors, or an
    # auto-accept toggle.
    background_bash_count_pattern=_NEVER,
    background_bash_marker=_NEVER_SUBSTRING,
    single_task_running_marker=_NEVER_SUBSTRING,
    subagent_count_pattern=_NEVER,
    monitor_count_pattern=_NEVER,
    auto_accept_pattern=_NEVER,
)
