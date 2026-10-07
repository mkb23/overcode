"""
Help overlay (h / ?): every key, and what the status colours mean.

Nothing here names a key. The rows come from the command palette's
registry (command_palette.COMMANDS, grouped by its CATEGORIES, titled as
the palette titles them) and the keys from the effective keymap (#510), so
help, palette and keymap cannot drift: remap a key and help shows the new
one; bind a key to an action the palette doesn't list and it still appears
(under App). The other key scopes — command bar, palette, prompt lab —
follow, then the status reference.

Layout is responsive (#510): 1, 2 or 3 columns by width, a compact form on
narrow terminals (shorter key column, unbound commands left out), and the
overlay scrolls when it is taller than the screen (↑/↓ or the next/previous
agent keys, PgUp/PgDn, Home/End).

build_help() is pure (width + keymap in, Rich Text out) so layout is
testable without an app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from rich.cells import cell_len
from rich.text import Text
from textual.containers import ScrollableContainer
from textual.widget import Widget

from . import dialog_style as ds


# ---------------------------------------------------------------------------
# Colours — every colour this overlay uses. Point these at a theme palette
# (#508) rather than writing colours further down.
# ---------------------------------------------------------------------------

C_TITLE = f"bold {ds.ACCENT}"          # the overlay title
C_SECTION = f"bold {ds.TEXT}"          # section headings
C_RULE = ds.STATE_OTHER                # rule under a heading
C_KEY = ds.KEY                         # a key
C_TEXT = ds.TEXT                       # what a key does
C_MUTED = ds.MUTED                     # notes, descriptions
C_UNBOUND = ds.STATE_OTHER             # commands with no key (palette only)

# Status colours — the timeline's and the agent list's (status_constants).
C_RUNNING = "green"
C_WAITING = "yellow"
C_APPROVAL = "orange1"
C_BLOCKED = "red"
C_ERROR = "magenta"
C_ASLEEP = "dim"


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

GAP = 3                  # cells between columns
COMPACT_BELOW = 64       # narrower than this: compact form
TWO_COLUMNS_AT = 100     # at least this wide: two columns
THREE_COLUMNS_AT = 150   # at least this wide: three columns
KEY_COL_MAX = 10         # widest key column (labels beyond it are cropped)
UNBOUND = "·"            # key column of a palette-only command


def help_columns(width: int) -> int:
    """How many columns the overlay uses at a given width."""
    if width >= THREE_COLUMNS_AT:
        return 3
    if width >= TWO_COLUMNS_AT:
        return 2
    return 1


def is_compact(width: int) -> bool:
    return width < COMPACT_BELOW


# ---------------------------------------------------------------------------
# Content model
# ---------------------------------------------------------------------------

@dataclass
class HelpRow:
    """A key (or keys, as one label) and what it does."""
    keys: str
    text: str
    unbound: bool = False


@dataclass
class HelpSection:
    title: str
    items: List[Union[HelpRow, Text]] = field(default_factory=list)
    kind: str = "keys"   # "keys" or "reference"


_SEND_DIGITS = tuple(f"send_{i}_to_focused" for i in range(1, 6))


def _scope_rows(km: Any, scope: str) -> List[HelpRow]:
    """A scope's bindings, one row per action, in binding order."""
    order: List[str] = []
    labels: Dict[str, List[str]] = {}
    desc: Dict[str, str] = {}
    from ..command_palette import key_label
    for b in km.bindings(scope):
        if b.action not in labels:
            order.append(b.action)
            labels[b.action] = []
            desc[b.action] = b.description or b.action.replace("_", " ")
        labels[b.action].append(key_label(b.key))
    return [HelpRow("/".join(labels[a]), desc[a]) for a in order]


def key_sections(km: Any, compact: bool = False,
                 toggle_label: Optional[str] = None) -> List[HelpSection]:
    """The key sections: palette categories, then the other key scopes."""
    from ..command_palette import CATEGORIES, COMMANDS

    by_cat: Dict[str, List[HelpRow]] = {c: [] for c in CATEGORIES}
    listed = set()
    digits_bound = all(km.keys_for(a) == [str(i)] for i, a in enumerate(_SEND_DIGITS, 1))
    for cmd in COMMANDS:
        listed.add(cmd.action)
        if digits_bound and cmd.action in _SEND_DIGITS:
            if cmd.action == _SEND_DIGITS[0]:
                by_cat[cmd.category].append(HelpRow("1-5", "Send 1–5 (numbered choices)"))
            continue
        label = km.label(cmd.action)
        if not label and compact:
            continue
        by_cat[cmd.category].append(HelpRow(label or UNBOUND, cmd.title, unbound=not label))

    # Bound actions the palette does not list (the palette key itself, or a
    # user binding): still shown, so every key in the keymap is in help.
    extra: Dict[str, Tuple[List[str], str]] = {}
    from ..command_palette import key_label
    for b in km.bindings("app"):
        if b.action in listed:
            continue
        keys, _ = extra.setdefault(b.action, ([], b.description or b.action.replace("_", " ")))
        keys.append(key_label(b.key))
    for action, (keys, desc) in extra.items():
        by_cat.setdefault("App", []).insert(0, HelpRow("/".join(keys), desc))

    sections = [HelpSection(cat.upper(), rows) for cat, rows in by_cat.items() if rows]

    bar = [HelpRow("Enter", "Send instruction")] + _scope_rows(km, "command_bar")
    open_bar = km.label("focus_command_bar")
    sections.append(HelpSection(f"COMMAND BAR ({open_bar})" if open_bar else "COMMAND BAR", bar))
    sections.append(HelpSection(
        f"COMMAND PALETTE ({km.label('command_palette') or km.label('jump_to_agent')})",
        _scope_rows(km, "command_palette")
        + [HelpRow(">", "Commands, from the agent list")]))
    if not compact:
        sections.append(HelpSection("SUMMARY PROMPT LAB", _scope_rows(km, "summary_prompt_lab")))

    split = "/".join(filter(None, (km.label("split_shrink"), km.label("split_grow"))))
    tmux_rows = [HelpRow(toggle_label or "Tab", "Switch pane (TUI ⇄ agent)")]
    if split:
        tmux_rows.append(HelpRow(split, "Resize split"))
    tmux_rows += [
        HelpRow("M-j/M-k", "Next / prev agent (either pane)"),
        HelpRow("M-b", "Go to bell (from the agent pane)"),
        HelpRow("PgUp/PgDn", "Scrollback"),
    ]
    sections.append(HelpSection("TMUX SPLIT (overcode tmux)", tmux_rows))
    return sections


def reference_sections(km: Any, compact: bool = False) -> List[HelpSection]:
    """Status colours, indicators, emoji and the timeline legend."""
    from ..status_constants import _safe_emoji

    def status(emoji: str, char: str, colour: str, name: str, desc: str) -> List[Text]:
        head = Text()
        head.append(f"{_safe_emoji(emoji)} ")
        head.append(char, style=colour)
        head.append(f"  {name}", style=f"bold {C_TEXT}")
        if compact:
            return [head]
        return [head, Text(f"     {desc}", style=C_MUTED)]

    sleep_key = km.label("toggle_sleep")
    done_key = km.label("toggle_show_done")
    statuses: List[Text] = []
    for args in (
        ("🟢", "█", C_RUNNING, "Running", "Actively working on a task"),
        ("💚", "█", C_RUNNING, "Running (heartbeat)", "Auto-resumed by heartbeat"),
        ("💛", "▒", C_WAITING, "Waiting (heartbeat)", "Paused — heartbeat will auto-resume"),
        ("🟠", "▒", C_APPROVAL, "Waiting (approval)", "Needs plan or tool use approval"),
        ("🔴", "░", C_BLOCKED, "Waiting (user)", "Blocked — needs human input"),
        ("🟡", "█", C_WAITING, "Busy (sleep/monitor)",
         "Sleeping or watching a live Monitor — will self-resume"),
        ("🟣", "▓", C_ERROR, "Error", "API timeout, rate limit, etc."),
        ("💤", "░", C_ASLEEP, "Asleep",
         f"Paused by human  ({sleep_key} to toggle)" if sleep_key else "Paused by human"),
        ("⚫", "×", C_ASLEEP, "Terminated", "Process exited, shell showing"),
        ("✓", "✓", C_RUNNING, "Done",
         f"Child agent completed ({done_key} to show)" if done_key else "Child agent completed"),
    ):
        statuses += status(*args)

    def plain(emoji: str, desc: str, style: str = C_TEXT) -> Text:
        t = Text()
        t.append(f"{_safe_emoji(emoji)}  ")
        t.append(desc, style=style)
        return t

    indicators = [plain(e, d) for e, d in (
        ("🔔", "Unvisited stall (bell)"), ("🤿", "Subagent count"),
        ("🐚", "Background bash count"), ("👶", "Child agent count"),
        ("📋", "Standing orders active"), ("✓ ", "Standing orders complete"),
        ("🤝", "Agent teams enabled"),
    )]

    from ..summary_columns import SKILL_EMOJI_DEFAULT, TOOL_EMOJI, get_skill_emoji
    skills: List[Text] = []
    skill_emoji = get_skill_emoji()
    if skill_emoji:
        skills = [plain(e, n) for n, e in sorted(skill_emoji.items())]
        skills.append(plain(SKILL_EMOJI_DEFAULT, "(unknown skill)", C_MUTED))
    else:
        skills.append(Text("No skills configured", style=C_MUTED))
    if not compact:
        skills += [Text("Configure in ~/.overcode/config.yaml", style=C_MUTED),
                   Text("  skill_emoji:", style=C_MUTED),
                   Text("    my-skill: 🎯", style=C_MUTED)]

    tools = [plain(e, n) for n, e in sorted(TOOL_EMOJI.items())]

    def legend(*pairs: Tuple[str, str, str]) -> Text:
        t = Text()
        for char, colour, what in pairs:
            t.append(char, style=colour)
            t.append(f" {what}  ", style=C_MUTED)
        return t

    timeline = [
        legend(("█", C_RUNNING, "active"), (_safe_emoji("💚"), "", "heartbeat start")),
        legend(("▒", C_WAITING, "waiting"), ("░", C_BLOCKED, "blocked"), ("▓", C_ERROR, "error")),
        legend(("×", C_ASLEEP, "exited"), ("░", C_ASLEEP, "asleep"), ("─", C_ASLEEP, "no data")),
    ]
    return [
        HelpSection("AGENT STATUSES", statuses, "reference"),
        HelpSection("SPECIAL INDICATORS", indicators, "reference"),
        HelpSection("TIMELINE LEGEND", timeline, "reference"),
        HelpSection("SKILL EMOJI", skills, "reference"),
        HelpSection("TOOL EMOJI", tools, "reference"),
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_section(section: HelpSection, width: int, compact: bool = False) -> List[Text]:
    """A section as lines exactly `width` cells wide."""
    lines: List[Text] = []
    head = Text(section.title, style=C_SECTION)
    if not compact:
        head.append(" ")
        head.append("─" * max(0, width - cell_len(head.plain)), style=C_RULE)
    lines.append(ds.finish(head, width))
    rows = [i for i in section.items if isinstance(i, HelpRow)]
    key_w = min(KEY_COL_MAX if not compact else 7,
                max((cell_len(r.keys) for r in rows), default=1))
    for item in section.items:
        if isinstance(item, HelpRow):
            line = Text()
            key_style = C_UNBOUND if item.unbound else C_KEY
            keys = item.keys if cell_len(item.keys) <= key_w else item.keys[: key_w - 1] + "…"
            line.append(f"{keys:<{key_w}}", style=key_style)
            line.append(" ")
            line.append(item.text, style=C_UNBOUND if item.unbound else C_TEXT)
            lines.append(ds.finish(line, width))
        else:
            lines.append(ds.finish(item.copy(), width))
    return lines


def _distribute(blocks: Sequence[List[Text]], columns: int) -> List[List[List[Text]]]:
    """Split blocks (sections, in order) into `columns` columns of similar
    height, never splitting a block."""
    if columns <= 1:
        return [list(blocks)]
    heights = [len(b) + 1 for b in blocks]  # +1: blank line between sections
    total = sum(heights)
    target = total / columns
    cols: List[List[List[Text]]] = [[] for _ in range(columns)]
    col, height = 0, 0
    for block, h in zip(blocks, heights):
        # Move on when this block would overshoot by more than half of
        # itself and there is a column left to take it.
        if cols[col] and col < columns - 1 and height + h / 2 > target:
            col, height = col + 1, 0
        cols[col].append(block)
        height += h
    return cols


def header(width: int, preset: str, km: Any) -> List[Text]:
    title = Text(" OVERCODE HELP ", style=C_TITLE)
    close = "/".join(filter(None, (km.label("toggle_help"), "Esc")))
    right = Text()
    if preset and preset != "default":
        right.append(f"keys: {preset} preset  ·  ", style=C_MUTED)
    right.append(f"{close} close  ·  ↑↓ PgUp PgDn scroll", style=C_MUTED)
    if cell_len(title.plain) + cell_len(right.plain) + 2 <= width:
        return [ds.spread(title, right, width), Text(" " * width)]
    return [ds.finish(title, width), ds.finish(right, width), Text(" " * width)]


def build_help(width: int, km: Any = None, toggle_label: Optional[str] = None) -> Text:
    """The whole overlay at `width` cells: header, then sections in columns."""
    if km is None:
        from ..keymap import active
        km = active()
    width = max(20, int(width))
    compact = is_compact(width)
    n = help_columns(width)
    col_w = (width - GAP * (n - 1)) // n

    sections = key_sections(km, compact, toggle_label) + reference_sections(km, compact)
    blocks = [render_section(s, col_w, compact) for s in sections]
    columns = _distribute(blocks, n)

    col_lines: List[List[Text]] = []
    for col in columns:
        lines: List[Text] = []
        for i, block in enumerate(col):
            if i:
                lines.append(Text(" " * col_w))
            lines.extend(block)
        col_lines.append(lines)
    rows = max((len(c) for c in col_lines), default=0)

    out = Text(no_wrap=True, overflow="crop")
    for line in header(width, getattr(km, "preset", ""), km):
        out.append_text(line)
        out.append("\n")
    blank = Text(" " * col_w)
    for r in range(rows):
        line = Text()
        for c, lines in enumerate(col_lines):
            if c:
                line.append(" " * GAP)
            line.append_text(lines[r] if r < len(lines) else blank)
        out.append_text(ds.finish(line, width))
        if r < rows - 1:
            out.append("\n")
    return out


def _toggle_label() -> str:
    try:
        from ..config import get_tmux_toggle_key
        from ..cli.split import DEFAULT_TOGGLE_KEY, TOGGLE_KEY_CHOICES
        key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
        label = next((lbl for lbl, k in TOGGLE_KEY_CHOICES if k == key), key)
        return label.split(" ")[0]
    except Exception:
        return "Tab"


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class HelpBody(Widget):
    """The overlay's content, laid out for the width it is given."""

    DEFAULT_CSS = """
    HelpBody {
        height: auto;
        width: 100%;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache: Dict[int, Text] = {}

    def _content(self, width: int) -> Text:
        width = width or 120
        text = self._cache.get(width)
        if text is None:
            text = build_help(width, toggle_label=_toggle_label())
            self._cache = {width: text}
        return text

    def invalidate(self) -> None:
        self._cache = {}
        self.refresh(layout=True)

    def get_content_height(self, container: Any, viewport: Any, width: int) -> int:
        return len(self._content(width).plain.split("\n"))

    def render(self) -> Text:
        return self._content(self.size.width)


class HelpOverlay(ScrollableContainer, can_focus=False):
    """Help overlay: every key and the status reference, scrollable."""

    def compose(self):
        yield HelpBody(id="help-body")

    def _body(self) -> Optional[HelpBody]:
        try:
            return self.query_one(HelpBody)
        except Exception:
            return None

    def refresh_content(self) -> None:
        """Rebuild (keymap, toggle key or emoji settings changed)."""
        body = self._body()
        if body is not None:
            body.invalidate()

    def render_text(self, width: int = 120) -> Text:
        """The overlay's text at a width, without mounting (tests, dumps)."""
        return build_help(width, toggle_label=_toggle_label())

    def on_resize(self, event: Any) -> None:
        self.refresh_content()

    def scroll_lines(self, n: int) -> None:
        self.scroll_relative(y=n, animate=False)

    def scroll_key(self, key: str) -> bool:
        """Scroll for PgUp/PgDn/Home/End/↑/↓; True when the key was used."""
        page = max(1, self.size.height - 2)
        if key == "pagedown":
            self.scroll_relative(y=page, animate=False)
        elif key == "pageup":
            self.scroll_relative(y=-page, animate=False)
        elif key == "home":
            self.scroll_home(animate=False)
        elif key == "end":
            self.scroll_end(animate=False)
        else:
            return False
        return True
