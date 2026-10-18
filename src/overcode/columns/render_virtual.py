"""Delegated-work rows on the column grid (0.6.1 observability, phase 2).

An expanded parent's delegated agents and its "+k more" line
(``swarm_view.SwarmLine``) render through the same columns as agent rows,
so they line up cell for cell. ``ColumnContext.for_virtual`` builds a
row's context from the parent's (view settings only) and the row the
engine published; a column with a ``virtual_render`` (below) fills its
cell from that, every other column shows its placeholder: a dim "-" at
the right edge of the agents' text in that column, blank where the agents'
cells are blank, so the "-" sits where a value would (``placeholder``).

What a subagent fills: status (its light), time in state and uptime (since
it started, from the published start; its run time once finished), RES
(✅ / ❌, the result text in the hover), SUBA (a summary line's counts),
KIND, NAME (tree connector, kind marker, title), TYPE (its agent type, when
the members on screen are of two or more), MDL (its own model, or the
parent's dim when it inherits it), TOK, $ and Q% (its own spend, from its
transcript), BILL (the parent's code: its spend is billed there). The
summary column (``l``) maps by mode (``virtual_summary``).

Nothing here is published as a countdown: durations are worked out from
absolute times as the agent rows' are, and only on the row clock.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import List, Optional, Tuple

from rich.cells import cell_len, set_cell_size

from ..status_constants import RESULT_FAILURE, RESULT_SUCCESS, emoji_or_ascii
from ..swarm_view import (
    COUNT_GLYPHS, SwarmLine, light_glyph, marker_glyph, run_seconds,
)
from ..tui_helpers import format_cost, format_duration, format_tokens
from .context import ColumnContext, ColumnOutput

# The lines that are one agent (the rest are summaries)
_LIT = ("member",)
# KIND column words (5 cells, as "agent" / "child")
KIND_SHORT = {"subagent": "sub", "fork": "fork", "teammate": "team", "workflow": "wf"}

# The clock the durations read (tests move it)
_now = time.time

# Row backgrounds: an agent row's, and the highlighted one's
ROW_BG = "on #0d2137"
SELECTED_BG = "on #1a3a50"


def virtual_context(line: SwarmLine, parent: ColumnContext, selected: bool = False) -> ColumnContext:
    """``ColumnContext.for_virtual``: the parent's settings, the row's values."""
    row = line.row if line.kind in _LIT else {}
    symbol, style = light_glyph(row, parent.emoji_free) if row else ("", "")
    result = row.get("result") if row.get("light") == "done" else None
    # No model of its own: it runs on the parent's (drawn dim)
    model = (row.get("model") or parent.model or "") if row else ""
    return replace(
        parent,
        virtual=line, bg="", has_focus=selected, is_list_mode=True, is_unvisited_stalled=False,
        status_symbol=symbol, status_color=f"bold {style}".strip(), status="",
        display_name=line.label, model=model, effort="",
        claude_stats=None, git_diff_stats=None, git_untracked_count=None,
        status_changed_at=None, status_detail=None, sleep_wake_estimate=None,
        task_result=(RESULT_FAILURE if result == "failed" else RESULT_SUCCESS) if result else None,
        task_reason=row.get("result_text") or ("failed" if result == "failed" else "done"
                                               if result else ""),
        child_rollup=None, swarm_rollup=None, swarm_expanded=False, subagent_cost_usd=None,
        subtree_cost_usd=0.0, window_burn=None, pr_number=None, oversight_deadline=None,
        live_subagent_count=0, background_bash_count=0, child_count=0, job_count=0,
    )


def _line(ctx: ColumnContext) -> SwarmLine:
    return ctx.virtual  # type: ignore[return-value]


def _lit_row(ctx: ColumnContext) -> dict:
    line = _line(ctx)
    return line.row if line.kind in _LIT else {}


# ---------------------------------------------------------------------------
# Durations: from published times, read against the clock here
# ---------------------------------------------------------------------------

def virtual_seconds(line: SwarmLine, now: float) -> Optional[float]:
    """How long a member (or a workflow run) has been going, or ran."""
    if line.kind not in _LIT:
        return None
    row = line.row
    if row.get("light") == "done":
        return run_seconds(row)
    start = row.get("started_at")
    if isinstance(start, (int, float)) and not isinstance(start, bool):
        return max(0.0, now - start)
    return None


def ticks(line: SwarmLine) -> bool:
    """Whether a line's durations move with the clock (a live, timed one)."""
    row = line.row
    return (line.kind in _LIT and row.get("light") != "done"
            and isinstance(row.get("started_at"), (int, float)))


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

def render_virtual_status(ctx: ColumnContext) -> ColumnOutput:
    """The member's or group's light (🟢 🟠 🟡 ⚪, ✅ / ❌ once finished)."""
    if not _lit_row(ctx):
        return None
    from .render_identity import render_status_symbol
    return render_status_symbol(ctx)


def render_virtual_time(ctx: ColumnContext) -> ColumnOutput:
    """Time in state: since it started; its run time once finished."""
    seconds = virtual_seconds(_line(ctx), _now())
    if seconds is None:
        return None
    return [(f"{format_duration(seconds):>5} ", ctx.status_color)]


def render_virtual_uptime(ctx: ColumnContext) -> ColumnOutput:
    seconds = virtual_seconds(_line(ctx), _now())
    if seconds is None:
        return None
    return [(f" ↑{format_duration(seconds):>5}", "bold white")]


def render_virtual_result(ctx: ColumnContext) -> ColumnOutput:
    """RES: ✅ / ❌ and the result text, as a child agent's."""
    if not ctx.task_result:
        return None
    from .render_supervision import render_task_result
    return render_task_result(ctx)


def hover_virtual_result(ctx: ColumnContext):
    if not ctx.task_result:
        return None
    from rich.text import Text
    row = _lit_row(ctx)
    glyph = emoji_or_ascii("❌" if ctx.task_result == RESULT_FAILURE else "✅", ctx.emoji_free)
    ran = run_seconds(row)
    text = Text(f"{glyph} {row.get('result_text') or ctx.task_reason}")
    if ran is not None:
        text.append(f"\nran {format_duration(ran)}", style="dim")
    return text


def _count_text(ctx: ColumnContext, key: str, n: int) -> str:
    glyph = dict(COUNT_GLYPHS)[key]
    return f"{glyph}{n}" if key == "more" else f"{ctx.e(glyph).rstrip()}{n}"


def render_virtual_subagents(ctx: ColumnContext) -> ColumnOutput:
    """SUBA: a summary line's counts, glyph then count, no total, laid out
    as the agent's cell ("▸   ✅25 ❌1")."""
    line = _line(ctx)
    if not line.counts:
        return None
    marker = " " * cell_len(ctx.e("👥"))
    text = f"{line.fold or ' '}{marker} " + " ".join(_count_text(ctx, k, n) for k, n in line.counts)
    return [(text, "dim magenta")]


def render_virtual_kind(ctx: ColumnContext) -> ColumnOutput:
    line = _line(ctx)
    if line.kind != "member":
        return None
    word = KIND_SHORT.get(line.row.get("kind"), "sub")
    return [(f"{word:<5}", "dim")]


def _fit(segments: List[Tuple[str, str]], width: int) -> List[Tuple[str, str]]:
    """Crop or pad segments to exactly ``width`` cells."""
    out, used = [], 0
    for text, style in segments:
        room = width - used
        if room <= 0:
            break
        w = cell_len(text)
        if w > room:
            text = set_cell_size(text, room)
            w = room
        out.append((text, style))
        used += w
    if used < width:
        out.append((" " * (width - used), ""))
    return out


def render_virtual_name(ctx: ColumnContext) -> ColumnOutput:
    """NAME: tree connector, kind marker, title, as wide as an agent's name."""
    line = _line(ctx)
    done = line.row.get("light") == "done"
    if line.kind == "member":
        label_style, marker_style = ("dim", "dim") if done else ("bold", "magenta")
    else:
        label_style, marker_style = "dim", ""
    segs = [(line.connector, "dim")]
    if line.marker:
        segs.append((marker_glyph(line.marker, ctx.emoji_free) + " ", marker_style))
    segs.append((line.label, label_style))
    return _fit(segs, max(1, ctx.max_name_width))


def hover_virtual_name(ctx: ColumnContext):
    from rich.text import Text
    line = _line(ctx)
    text = Text(line.label, style="bold")
    if line.context:
        text.append(f"\n{line.context}", style="dim")
    return text


def render_virtual_model(ctx: ColumnContext) -> ColumnOutput:
    """MDL: its own model; the parent's, dim, when it inherits it."""
    if not ctx.model:
        return None
    from .render_context import render_model
    segments = render_model(ctx)
    if _lit_row(ctx).get("model_inherited"):
        return [(text, "dim") for text, _ in segments]
    return segments


def render_virtual_type(ctx: ColumnContext) -> ColumnOutput:
    """TYPE: what kind of agent the member is (``swarm_view.type_label``),
    only while the members on screen are of two or more types."""
    line = _line(ctx)
    if not ctx.fleet.swarm_types_shown or line.kind != "member" or not line.type_label:
        return None
    done = line.row.get("light") == "done"
    return [(f" {line.type_label}", "dim" if done else "cyan")]


def render_virtual_quota(ctx: ColumnContext) -> ColumnOutput:
    """Q%: the member's points of the weekly quota (its whole run)."""
    pp = _lit_row(ctx).get("quota_pp")
    if not isinstance(pp, (int, float)) or isinstance(pp, bool):
        return None
    from ..quota_model import format_pp
    from .render_usage import QUOTA_W
    prior = (ctx.quota or {}).get("conf") == "prior"
    return [(format_pp(pp).rjust(QUOTA_W), "orange1" if prior else "bold orange1")]


def render_virtual_billing(ctx: ColumnContext) -> ColumnOutput:
    """BILL: the parent's code, dim (a member's spend is billed with it)."""
    if not _lit_row(ctx):
        return None
    from .render_usage import render_billing_code
    return [(text, "dim") for text, _ in render_billing_code(ctx)]


def render_virtual_tokens(ctx: ColumnContext) -> ColumnOutput:
    tokens = _lit_row(ctx).get("tokens")
    if not tokens:
        return None
    return [(f" Σ{format_tokens(tokens):>6}", "bold orange1")]


def render_virtual_cost(ctx: ColumnContext) -> ColumnOutput:
    cost = _lit_row(ctx).get("cost_usd")
    if not cost:
        return None
    return [(f" {format_cost(cost):>6}", "bold orange1")]


def render_virtual_expand(ctx: ColumnContext) -> ColumnOutput:
    """The highlight's arrow, as on the focused agent's row."""
    return [("→ " if ctx.has_focus else "  ", ctx.status_color or "bold")]


# ---------------------------------------------------------------------------
# Placeholders, the summary column, and the line
# ---------------------------------------------------------------------------

def column_edges(cell_rows) -> List[int]:
    """Per column, the right edge of the widest text in it (trailing spaces
    off): where a virtual row's "-" goes. 0: every cell blank."""
    if not cell_rows:
        return []
    edges = [0] * max(len(r) for r in cell_rows)
    for row in cell_rows:
        for i, cell in enumerate(row):
            w = cell_len(cell.plain.rstrip())
            if w > edges[i]:
                edges[i] = w
    return edges


def placeholder(edge: int) -> List[Tuple[str, str]]:
    """A column that does not apply: a dim "-" where values end, or blank."""
    return [(" " * (edge - 1) + "-", "dim")] if edge > 0 else []


def virtual_summary(line: SwarmLine, mode: str, emoji_free: bool = False) -> Tuple[str, str]:
    """The summary column (the ``l`` cycle) for a virtual row: (text, style).

    A member, by mode:
      ai_short      💬 what it is doing (its activity, or what its light
                    means: needs approval, idle, background work), or its
                    result text and run time once finished
      ai_long       📖 its agent type, kind and phase, then the same
      orders        🎯 its task as given (the full title)
      last_command  ⌨ its last tool call
      annotation, heartbeat: "-" (a subagent has neither)
    A summary line ("+k more") shows its own text in every mode.
    """
    e = lambda c: emoji_or_ascii(c, emoji_free)  # noqa: E731
    if line.kind != "member":
        return line.summary, "dim italic" if line.kind == "more" else "cyan"
    done = line.row.get("light") == "done"
    style = "dim italic" if done else "italic"
    if mode == "ai_long":
        lead = f"{line.context} · " if line.context else ""
        return f"{e('📖')} {lead}{line.summary or '-'}", style
    if mode == "orders":
        return f"{e('🎯')} {line.label}", style
    if mode == "last_command":
        last = line.row.get("activity")
        return (f"{e('⌨')} {last}", style) if last else ("-", "dim")
    if mode in ("annotation", "heartbeat"):
        return "-", "dim"
    return (f"{e('💬')} {line.summary}", style) if line.summary else ("-", "dim")
