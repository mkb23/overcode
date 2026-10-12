"""SummaryColumn, the detail-level sets and the simple-renderer factories."""

from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from .context import ColumnContext, ColumnOutput

if TYPE_CHECKING:
    from rich.text import Text


# ---------------------------------------------------------------------------
# Detail-level convenience sets
# ---------------------------------------------------------------------------
ALL = {"low", "med", "high", "full"}
MED_PLUS = {"med", "high", "full"}
HIGH_PLUS = {"high", "full"}


# ---------------------------------------------------------------------------
# SummaryColumn definition
# ---------------------------------------------------------------------------
def plain_from_render(
    render: Callable[[ColumnContext], ColumnOutput],
) -> Callable[[ColumnContext], Optional[str]]:
    """A plain renderer from a styled one: the segments' text joined and
    trimmed; None when the cell is empty or shows only the "-" placeholder."""
    def render_plain(ctx: ColumnContext) -> Optional[str]:
        segments = render(ctx)
        if not segments:
            return None
        text = "".join(t for t, _ in segments).strip()
        return None if text in ("", "-") else text
    return render_plain


@dataclass
class SummaryColumn:
    """Declarative definition of a summary line column."""

    id: str
    group: str  # Group ID from summary_groups.py
    detail_levels: set  # Which detail levels show this column
    render: Callable[[ColumnContext], ColumnOutput]
    label: str = ""  # Human-readable label for CLI (e.g., "Status", "Uptime")
    # Plain text for the CLI and uniform_columns; None = no value. Defaults
    # to the styled cell's text, trimmed, with "-" (no data) as None. Give an
    # explicit one only where the CLI wants different words (plain_from_render).
    render_plain: Optional[Callable[[ColumnContext], Optional[str]]] = None
    placeholder_width: int = 0  # When visible but render returns None, pad with N spaces
    visible: Optional[Callable[[ColumnContext], bool]] = None  # App-level visibility gate
    header: str = ""  # Short header label for column header row (e.g., "UPT", "TOK")
    name: str = ""  # Human-readable display name for config modal
    cli_only: bool = False  # True for synthetic aggregate lines used only by the CLI
    # Header tooltip and C-modal help line (#477). A test asserts every TUI
    # column has one, so a new column cannot ship without it.
    description: str = ""
    # Sort value for this column (#487); None = not sortable. Rows whose key
    # is None always sort last.
    sort_key: Optional[Callable[[ColumnContext], object]] = None
    sort_desc: bool = False  # Natural direction: True = largest first
    # Says nothing when every agent shows the same value (one host, one
    # model, one repo…): hidden until the fleet differs (uniform_columns),
    # unless the user switched it on explicitly. (backend also has its own
    # older gate, mixed_backends, which hides its placeholder too.)
    hide_when_uniform: bool = False
    # Popup for a row's cell, naming what its emoji stand for. None = no popup.
    hover: Optional[Callable[[ColumnContext], Optional["Text"]]] = None  # noqa: F821
    # Reads the clock: its cell can change with nothing else changing (a
    # duration or countdown). The TUI's 250 ms row clock redraws only these
    # cells; every other cell is rebuilt only when the row's inputs change.
    ticks: bool = False

    def __post_init__(self):
        if self.render_plain is None:
            self.render_plain = plain_from_render(self.render)
        if self.hide_when_uniform:
            self.description = (self.description + " Hidden while every agent shows the same value.").strip()


def _get_field(ctx: ColumnContext, field: str):
    """Resolve a dotted field path, e.g. 'stats.steers_count'."""
    obj = ctx
    for part in field.split("."):
        obj = getattr(obj, part)
    return obj


def _make_simple_render(
    field: str,
    formatter: Optional[Callable] = None,
    format_str: str = "{v}",
    colored_style: str = "bold",
) -> Callable[[ColumnContext], ColumnOutput]:
    """Factory for single-segment render functions.

    Args:
        field: Attribute path on ColumnContext (dot-notation for nested)
        formatter: Optional callable to transform the raw value
        format_str: Format string with {v} placeholder for the formatted value
        colored_style: Rich style (bg suffix added automatically)
    """
    def render(ctx: ColumnContext) -> ColumnOutput:
        val = _get_field(ctx, field)
        if formatter is not None:
            val = formatter(val)
        return [(format_str.format(v=val), f"{colored_style}{ctx.bg}")]
    return render


def _make_simple_render_plain(
    field: str,
    formatter: Optional[Callable] = None,
    template: str = "{v}",
) -> Callable[[ColumnContext], Optional[str]]:
    """Factory for simple plain-text render functions.

    Args:
        field: Attribute path on ColumnContext (dot-notation for nested)
        formatter: Optional callable to transform the raw value
        template: Format string with {v} placeholder
    """
    def render(ctx: ColumnContext) -> Optional[str]:
        val = _get_field(ctx, field)
        if formatter is not None:
            val = formatter(val)
        return template.format(v=val)
    return render
