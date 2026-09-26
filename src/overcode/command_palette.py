"""
Command palette registry and matcher (#482).

Pure logic, no Textual: the list of commands the palette offers, how their
keys are labelled, how each stateful command reports its states, and the
fuzzy matcher that ranks them. The widget in tui_widgets/command_palette.py
only draws what this module decides.

Keys are never written here. They are read from SupervisorTUI.BINDINGS at
runtime, so the palette cannot drift from what a key actually does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StateView:
    """What a stateful command is set to right now.

    `options` is the cycle in order and `current` indexes it. A stepper with
    no fixed cycle (the baseline) has one option — its value — at index 0.
    `current` is None when the state is unknown or does not apply (no agent
    focused, no heartbeat configured).
    """
    options: Tuple[str, ...]
    current: Optional[int]
    note: str = ""  # e.g. the agent a per-agent state belongs to

    @property
    def current_label(self) -> Optional[str]:
        if self.current is None or not (0 <= self.current < len(self.options)):
            return None
        return self.options[self.current]


OFF_ON = ("off", "on")


def _toggle(value: Optional[bool], note: str = "") -> StateView:
    return StateView(OFF_ON, None if value is None else int(bool(value)), note)


def _cycle(options: Sequence[str], value: Any, values: Optional[Sequence[Any]] = None) -> StateView:
    values = list(values) if values is not None else list(options)
    try:
        idx: Optional[int] = values.index(value)
    except ValueError:
        idx = None
    return StateView(tuple(options), idx)


def _focused_session(app: Any):
    """The focused agent's Session, or None (jobs view, no agents)."""
    if getattr(app, "tui_mode", "agents") != "agents":
        return None
    try:
        widget = app._get_focused_widget()
    except Exception:
        return None
    return getattr(widget, "session", None)


def _widget_shown(app: Any, widget_id: str) -> Optional[bool]:
    try:
        return bool(app.query_one(f"#{widget_id}").display)
    except Exception:
        return None


def _agent_toggle(attr: str) -> Callable[[Any], StateView]:
    def state(app: Any) -> StateView:
        s = _focused_session(app)
        if s is None:
            return _toggle(None)
        return _toggle(bool(getattr(s, attr, False)), s.name)
    return state


def _heartbeat_state(app: Any) -> StateView:
    s = _focused_session(app)
    options = ("running", "paused")
    if s is None or not getattr(s, "heartbeat_enabled", False):
        return StateView(options, None, "no heartbeat" if s is not None else "")
    return StateView(options, int(bool(s.heartbeat_paused)), s.name)


def _detection_state(app: Any) -> StateView:
    s = _focused_session(app)
    options = ("hooks", "polling")
    if s is None:
        return StateView(options, None)
    try:
        from .status_detector_factory import resolve_session_detection_mode
        mode = resolve_session_detection_mode(s, app.detector.mode)
    except Exception:
        return StateView(options, None)
    return StateView(options, options.index(mode) if mode in options else None, s.name)


def _cost_state(app: Any) -> StateView:
    options = ("tokens", "cost", "joules", "all")
    try:
        level = app.SUMMARY_LEVELS[app.summary_level_index]
        idx = app._match_cost_preset(app._prefs.column_config.get(level, {}))
    except Exception:
        return StateView(options, None)
    return StateView(options, idx)


def _baseline_state(app: Any) -> StateView:
    minutes = getattr(app, "baseline_minutes", 0) or 0
    if minutes == 0:
        label = "now"
    elif minutes % 60 == 0:
        label = f"-{minutes // 60}h"
    elif minutes < 60:
        label = f"-{minutes}m"
    else:
        label = f"-{minutes // 60}h{minutes % 60}m"
    return StateView((label,), 0)


def _summarizer_state(app: Any) -> StateView:
    try:
        return _toggle(bool(app._summarizer.config.enabled))
    except Exception:
        return _toggle(None)


def _web_state(app: Any) -> StateView:
    try:
        from .web_server import is_web_server_running
        return _toggle(is_web_server_running(app.tmux_session))
    except Exception:
        return _toggle(None)


def _sort_state(app: Any) -> StateView:
    prefs = getattr(app, "_prefs", None)
    if prefs is None:
        return StateView(("?",), None)
    from .tui_logic import get_sort_mode_display_name, sort_descending
    label = get_sort_mode_display_name(prefs.sort_mode)
    if prefs.sort_mode != "by_tree":
        label += " ▼" if sort_descending(prefs.sort_mode, prefs.sort_reversed) else " ▲"
    return StateView((label,), 0)


def _sort_direction_state(app: Any) -> StateView:
    prefs = getattr(app, "_prefs", None)
    options = ("▲ asc", "▼ desc")
    if prefs is None or prefs.sort_mode == "by_tree":
        return StateView(options, None, "tree order" if prefs is not None else "")
    from .tui_logic import sort_descending
    return StateView(options, int(sort_descending(prefs.sort_mode, prefs.sort_reversed)))


def _notifications_state(app: Any) -> StateView:
    options = ("off", "sound", "banner", "both")
    try:
        return _cycle(options, app._notifier.mode)
    except Exception:
        return StateView(options, None)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaletteCommand:
    """One command the palette can run.

    action:   the SupervisorTUI action name (method `action_<name>`).
    title:    what the palette shows; nouns for toggles ("Preview pane"),
              since the state column already says off/on.
    keywords: extra words the matcher accepts but does not display.
    state:    reports the current state; None for one-shot commands.
    agent:    acts on the focused agent (its state is that agent's).
    """
    action: str
    title: str
    category: str
    keywords: str = ""
    state: Optional[Callable[[Any], StateView]] = field(default=None, compare=False)
    agent: bool = False

    @property
    def repeatable(self) -> bool:
        """Safe to run with the palette left open (Tab): changes a setting
        in place and opens nothing."""
        return self.state is not None


CATEGORIES = (
    "Navigate", "View", "Display", "Agent", "Send to agent",
    "Daemons", "Settings", "App",
)

_C = PaletteCommand

COMMANDS: Tuple[PaletteCommand, ...] = (
    # Navigate
    _C("jump_to_agent", "Jump to agent…", "Navigate", "switch go find goto"),
    _C("jump_to_attention", "Next agent needing you", "Navigate", "bell red stalled waiting"),
    _C("focus_next_session", "Next agent", "Navigate", "down"),
    _C("focus_previous_session", "Previous agent", "Navigate", "up"),
    _C("filter_by_tag", "Filter agents by tag…", "Navigate", "tags"),
    _C("toggle_tui_mode", "Agents / jobs view", "Navigate", "jobs mode",
       state=lambda app: _cycle(("agents", "jobs"), getattr(app, "tui_mode", "agents"))),
    _C("cycle_focal_repo", "Cycle focal repo", "Navigate", "workspace directory", agent=True),

    # View — panels and layout
    _C("toggle_preview", "Preview pane", "View", "output terminal",
       state=lambda app: _toggle(getattr(app, "preview_visible", None))),
    _C("expand_preview", "Fullscreen preview", "View", "expand scrollback zoom"),
    _C("toggle_timeline", "Timeline", "View", "tl history bar",
       state=lambda app: _toggle(_widget_shown(app, "timeline"))),
    _C("toggle_daemon", "Daemon panel", "View", "logs supervisor monitor",
       state=lambda app: _toggle(_widget_shown(app, "daemon-panel"))),
    _C("toggle_tui_log", "TUI diagnostic log", "View", "debug logs",
       state=lambda app: _toggle(_widget_shown(app, "tui-log-panel"))),
    _C("toggle_column_headers", "Column headers", "View", "header row",
       state=lambda app: _toggle(getattr(getattr(app, "_prefs", None), "show_column_headers", None))),
    _C("toggle_copy_mode", "Copy mode", "View", "mouse select clipboard text",
       state=lambda app: _toggle(getattr(app, "_copy_mode", False))),
    _C("toggle_tmux_sync", "Tmux pane sync", "View", "pane sync follow",
       state=lambda app: _toggle(getattr(app, "tmux_sync", None))),
    _C("resize_focused_window", "Resize agent to pane", "View", "fit size", agent=True),
    _C("split_grow", "Grow monitor pane", "View", "split bigger taller resize"),
    _C("split_shrink", "Shrink monitor pane", "View", "split smaller shorter resize"),
    _C("toggle_help", "Help and status legend", "View", "keyboard shortcuts cheat sheet"),

    # Display — what the agent list shows
    _C("cycle_summary", "Summary detail", "Display", "level verbosity",
       state=lambda app: _cycle(app.SUMMARY_LEVELS, getattr(app, "summary_level_index", None),
                                range(len(app.SUMMARY_LEVELS)))),
    _C("cycle_summary_content", "Summary content", "Display", "ai orders annotation heartbeat",
       state=lambda app: _cycle(("short", "context", "orders", "note", "heartbeat", "command"),
                                getattr(app, "summary_content_mode", None),
                                app.SUMMARY_CONTENT_MODES)),
    _C("choose_sort", "Sort agents by…", "Display", "order column", state=_sort_state),
    _C("reverse_sort", "Reverse sort", "Display", "order direction ascending descending flip",
       state=_sort_direction_state),
    _C("toggle_cost_display", "Cost units", "Display", "tokens dollars joules energy money",
       state=_cost_state),
    _C("cycle_timeline_hours", "Timeline scope", "Display", "hours range window",
       state=lambda app: _cycle(tuple(f"{h}h" for h in app.TIMELINE_PRESETS),
                                getattr(getattr(app, "_prefs", None), "timeline_hours", None),
                                app.TIMELINE_PRESETS)),
    _C("baseline_back", "Baseline 15 min earlier", "Display", "mean spin back", state=_baseline_state),
    _C("baseline_forward", "Baseline 15 min later", "Display", "mean spin forward", state=_baseline_state),
    _C("baseline_reset", "Reset baseline to now", "Display", "mean spin", state=_baseline_state),
    _C("toggle_show_terminated", "Show killed agents", "Display", "terminated ghost",
       state=lambda app: _toggle(getattr(app, "show_terminated", None))),
    _C("toggle_hide_asleep", "Hide sleeping agents", "Display", "asleep",
       state=lambda app: _toggle(getattr(app, "hide_asleep", None))),
    _C("toggle_show_done", "Show done child agents", "Display", "finished children",
       state=lambda app: _toggle(getattr(app, "show_done", None))),
    _C("toggle_collapse_children", "Fold / unfold children", "Display", "collapse expand tree", agent=True),
    _C("toggle_monochrome", "Monochrome", "Display", "black white colour color ansi",
       state=lambda app: _toggle(getattr(app, "monochrome", None))),
    _C("toggle_emoji_free", "Emoji-free", "Display", "ascii",
       state=lambda app: _toggle(getattr(app, "emoji_free", None))),
    _C("open_column_config", "Configure columns…", "Display", "fields"),

    # Agent — acts on the focused agent
    _C("new_agent", "New agent…", "Agent", "launch create start remote"),
    _C("rename_focused", "Rename agent…", "Agent", "name", agent=True),
    _C("fork_focused", "Fork agent", "Agent", "clone copy child", agent=True),
    _C("restart_focused", "Restart agent", "Agent", "revive", agent=True),
    _C("kill_focused", "Kill / clean up agent", "Agent", "stop delete remove", agent=True),
    _C("sync_to_main_and_clear", "Sync to main and clear", "Agent", "git reset", agent=True),
    _C("toggle_sleep", "Sleep", "Agent", "asleep wake pause", state=_agent_toggle("is_asleep"), agent=True),
    _C("toggle_heartbeat_pause", "Heartbeat", "Agent", "hb pause resume", state=_heartbeat_state, agent=True),
    _C("configure_heartbeat", "Heartbeat config…", "Agent", "interval instruction", agent=True),
    _C("focus_standing_orders", "Standing orders…", "Agent", "instructions", agent=True),
    _C("focus_human_annotation", "Annotation…", "Agent", "note comment", agent=True),
    _C("edit_agent_value", "Agent value…", "Agent", "priority", agent=True),
    _C("edit_cost_budget", "Cost budget…", "Agent", "money limit dollars", agent=True),
    _C("toggle_enhanced_context", "Enhanced context", "Agent", "hook inject",
       state=_agent_toggle("enhanced_context_enabled"), agent=True),
    _C("toggle_hook_detection", "Status detection", "Agent", "hooks polling mode",
       state=_detection_state, agent=True),
    _C("open_instruction_history", "Instruction history…", "Agent", "sent previous", agent=True),

    # Send to agent — keystrokes forwarded to the focused agent's pane
    _C("focus_command_bar", "Send instruction…", "Send to agent", "message prompt type", agent=True),
    _C("send_enter_to_focused", "Approve (send Enter)", "Send to agent", "yes accept confirm", agent=True),
    _C("send_escape_to_focused", "Interrupt (send Esc)", "Send to agent", "cancel stop", agent=True),
    _C("send_1_to_focused", "Send 1", "Send to agent", "option choice number", agent=True),
    _C("send_2_to_focused", "Send 2", "Send to agent", "option choice number", agent=True),
    _C("send_3_to_focused", "Send 3", "Send to agent", "option choice number", agent=True),
    _C("send_4_to_focused", "Send 4", "Send to agent", "option choice number", agent=True),
    _C("send_5_to_focused", "Send 5", "Send to agent", "option choice number", agent=True),
    _C("send_ctrl_o_to_focused", "Send Ctrl+O", "Send to agent", "passthru expand", agent=True),

    # Daemons and services
    _C("toggle_summarizer", "AI summarizer", "Daemons", "openai summary", state=_summarizer_state),
    _C("open_summary_prompt_lab", "AI summary prompts…", "Daemons", "summarizer edit tune lab"),
    _C("toggle_web_server", "Web dashboard", "Daemons", "server browser http", state=_web_state),
    _C("cycle_notifications", "macOS notifications", "Daemons", "sound banner alert",
       state=_notifications_state),
    _C("supervisor_start", "Start supervisor daemon", "Daemons", "run"),
    _C("supervisor_stop", "Stop supervisor daemon", "Daemons", "halt"),
    _C("monitor_restart", "Restart monitor daemon", "Daemons", "reload"),

    # Settings dialogs
    _C("open_new_agent_defaults", "New agent defaults…", "Settings", "launch config"),
    _C("open_sister_selection", "Sister visibility…", "Settings", "remote hosts machines"),
    _C("open_tmux_config", "Tmux toggle key…", "Settings", "split pane focus"),
    _C("open_passthru_config", "Passthru keys…", "Settings", "forward ctrl"),

    _C("quit", "Quit", "App", "exit close detach"),
)

# Commands the palette handles itself by switching list rather than closing.
MODE_SWITCHES = {"jump_to_agent": "agents", "filter_by_tag": "tags", "choose_sort": "sort"}


# ---------------------------------------------------------------------------
# Sort choices (#487)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SortChoice:
    """One row of the sort picker (S): a column, or tree order.

    `descending` is the direction choosing it would sort — the current
    direction for the active row, since choosing it again reverses.
    """
    mode: str
    name: str
    header: str = ""
    description: str = ""
    active: bool = False
    descending: bool = False


TREE_CHOICE_DESCRIPTION = "Parents with their children indented beneath (X folds)"


def sort_choices(sort_mode: str, sort_reversed: bool) -> List[SortChoice]:
    """Every sortable column in list order, then tree order."""
    from .summary_columns import SUMMARY_COLUMNS
    from .tui_logic import sort_descending, sort_mode_for_column

    choices: List[SortChoice] = []
    for col in SUMMARY_COLUMNS:
        if col.sort_key is None or col.cli_only:
            continue
        mode = sort_mode_for_column(col.id)
        active = mode == sort_mode
        choices.append(SortChoice(
            mode=mode, name=col.name or col.id, header=col.header,
            description=col.description, active=active,
            descending=sort_descending(mode, sort_reversed) if active else col.sort_desc,
        ))
    choices.append(SortChoice(
        mode="by_tree", name="Tree", description=TREE_CHOICE_DESCRIPTION,
        active=sort_mode == "by_tree",
    ))
    return choices


def filter_sort_choices(choices: Sequence[SortChoice], query: str) -> List[Tuple[SortChoice, Tuple[int, ...]]]:
    """Choices matching `query`, best first, with name positions to light.

    Each word must match the name (fuzzily, as commands do) or appear in
    the header code or description; an exact header ("cpu", "tok") ranks
    first.
    """
    query = query.strip()
    if not query:
        return [(c, ()) for c in choices]
    words = query.split()
    scored = []
    for i, c in enumerate(choices):
        total, positions = 0, set()
        for w in words:
            m = fuzzy_match(w, c.name)
            if c.header and w.lower() == c.header.lower():
                total += 2000
            elif m is not None:
                total += m[0]
                positions.update(m[1])
            elif w.lower() in f"{c.header} {c.description}".lower():
                total += 300
            else:
                total = None
                break
        if total is not None:
            scored.append((-total, i, c, tuple(sorted(positions))))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [(c, pos) for _, _, c, pos in scored]


# ---------------------------------------------------------------------------
# Key labels
# ---------------------------------------------------------------------------

_KEY_LABELS = {
    "left_square_bracket": "[", "right_square_bracket": "]", "backslash": "\\",
    "question_mark": "?", "colon": ":", "dollar_sign": "$", "comma": ",",
    "full_stop": ".", "less_than_sign": "<", "greater_than_sign": ">",
    "equals_sign": "=", "minus": "-", "slash": "/",
    "down": "↓", "up": "↑", "left": "←", "right": "→",
    "enter": "Enter", "escape": "Esc", "tab": "Tab", "space": "Space",
}


def key_label(key: str) -> str:
    """Display form of a Textual key name: `ctrl+p` → `^P`, `comma` → `,`."""
    if key.startswith("ctrl+"):
        return "^" + key_label(key[len("ctrl+"):]).upper()
    return _KEY_LABELS.get(key, key)


def keys_by_action(bindings: Iterable[Any]) -> Dict[str, List[str]]:
    """Map each action to its key labels, in binding order (`j` before `↓`)."""
    out: Dict[str, List[str]] = {}
    for b in bindings:
        key, action = (b[0], b[1]) if isinstance(b, tuple) else (b.key, b.action)
        for k in key.split(","):
            out.setdefault(action, []).append(key_label(k.strip()))
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def fuzzy_match(query: str, text: str) -> Optional[Tuple[int, Tuple[int, ...]]]:
    """Score `query` against `text`; None when it does not match.

    Two ways to match, VSCode-style:
      - a contiguous substring ("mary" in Summary), scored higher at a word
        start and higher still at the very start;
      - word prefixes: each character either starts a word or directly
        follows the previous matched character, so "sd" and "sumdet" find
        Summary detail but "sum" does not find "Stop supervisor daemon".
    Returns (score, positions) — positions index `text` for highlighting.
    """
    if not query:
        return (0, ())
    q, t = query.lower(), text.lower()

    def word_start(i: int) -> bool:
        return i == 0 or (not t[i - 1].isalnum() and t[i].isalnum()) or (
            text[i].isupper() and text[i - 1].islower())

    best = None
    start = t.find(q)
    while start != -1:
        score = 1000 - start + (400 if word_start(start) else 0) + (200 if start == 0 else 0)
        if best is None or score > best[0]:
            best = (score, tuple(range(start, start + len(q))))
        start = t.find(q, start + 1)
    if best is not None:
        return best

    # Word-prefix match: depth-first, trying the earliest candidate first
    # (titles are short and queries shorter, so this stays tiny).
    def search(qi: int, prev: int) -> Optional[List[int]]:
        if qi == len(q):
            return []
        j = t.find(q[qi], prev + 1)
        while j != -1:
            if j == prev + 1 or word_start(j):
                rest = search(qi + 1, j)
                if rest is not None:
                    return [j] + rest
            j = t.find(q[qi], j + 1)
        return None

    found = search(0, -1)
    if found is None or not word_start(found[0]):
        return None
    words = sum(1 for p in found if word_start(p))
    return (600 - 20 * words - found[0], tuple(found))


@dataclass(frozen=True)
class Match:
    command: PaletteCommand
    positions: Tuple[int, ...] = ()  # into command.title
    by_key: bool = False             # query named this command's key


def rank_commands(
    commands: Sequence[PaletteCommand],
    query: str,
    keymap: Dict[str, List[str]],
) -> List[Match]:
    """Commands matching `query`, best first.

    Space-separated words must all match; each is tried against the title
    (highlighted) and then the category and keywords (not highlighted).
    A query that is exactly one of a command's keys, case-sensitively,
    puts that command first — so "S" answers "what does S do?" and "$"
    finds the cost cycle, which no title contains.
    """
    query = query.strip()
    if not query:
        return [Match(c) for c in commands]
    order = {c.action: i for i, c in enumerate(commands)}
    scored: List[Tuple[int, int, Match]] = []
    words = query.split()
    # "^n" names the same key as "^N"; every other key is case-sensitive.
    key_query = "^" + query[1:].upper() if query.startswith("^") and len(query) > 1 else query
    for cmd in commands:
        if key_query in keymap.get(cmd.action, ()):
            scored.append((10_000, order[cmd.action], Match(cmd, (), by_key=True)))
            continue
        total: Optional[int] = 0
        positions: set = set()
        extra = f"{cmd.category} {cmd.keywords}".lower()
        for w in words:
            m = fuzzy_match(w, cmd.title)
            if m is not None:
                total += m[0]
                positions.update(m[1])
            elif w.lower() in extra:  # keywords match as substrings only
                total += 300
            else:
                total = None
                break
        if total is None:
            continue
        scored.append((total, order[cmd.action], Match(cmd, tuple(sorted(positions)))))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [m for _, _, m in scored]
