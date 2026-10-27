"""Git columns: repo, branch, diff, untracked files, PR."""

from typing import Optional

from ..tui_helpers import format_line_count
from .column import _make_simple_render_plain
from .context import (  # noqa: F401  (the repo cell's helpers, re-exported)
    WORKTREE_MARK, ColumnContext, ColumnOutput, names_match_repos,
    repo_cell_parts, repo_cell_width,
)


def render_repo_name(ctx: ColumnContext) -> ColumnOutput:
    if ctx.all_names_match_repos:
        return None
    w = ctx.max_repo_width
    worktree = ctx.worktree_label
    if not worktree:
        return [(f" {ctx.repo_name:<{w}}", f"bold dim{ctx.bg}")]
    pad = max(0, w - len(ctx.repo_name) - len(worktree))
    return [(f" {ctx.repo_name}", f"bold dim{ctx.bg}"),
            (f"{worktree}{' ' * pad}", f"cyan{ctx.bg}")]


def render_branch(ctx: ColumnContext) -> ColumnOutput:
    w = ctx.max_branch_width
    sep = ":" if not ctx.all_names_match_repos else " "
    return [(f"{sep}{ctx.branch:<{w}} ", f"bold dim{ctx.bg}")]


def render_git_diff(ctx: ColumnContext) -> ColumnOutput:
    if ctx.git_diff_stats:
        files, ins, dels = ctx.git_diff_stats
        if ctx.summary_detail in ("full", "high"):
            return [
                (f" Δ{files:>2}", f"bold magenta{ctx.bg}"),
                (f" +{format_line_count(ins):>5}", f"bold green{ctx.bg}"),
                (f" -{format_line_count(dels):>5}", f"bold red{ctx.bg}"),
            ]
        else:
            has_changes = files > 0
            return [(f" Δ{files:>2}", f"bold magenta{ctx.bg}" if has_changes else f"dim{ctx.bg}")]
    else:
        if ctx.summary_detail in ("full", "high"):
            return [(" Δ - +    - -    -", f"dim{ctx.bg}")]
        else:
            return [(" Δ -", f"dim{ctx.bg}")]


def render_git_untracked(ctx: ColumnContext) -> ColumnOutput:
    """Untracked file count (#455). Highlights when there are untracked files,
    so a 0-line diff doesn't hide work an agent has yet to commit."""
    n = ctx.git_untracked_count
    if n is None:
        return [(" U  -", f"dim{ctx.bg}")]
    if n > 0:
        return [(f" U{n:>3}", f"bold yellow{ctx.bg}")]
    return [(" U  0", f"dim{ctx.bg}")]


def render_git_untracked_plain(ctx: ColumnContext) -> Optional[str]:
    n = ctx.git_untracked_count
    if n is None or n == 0:
        return None
    return f"U{n}"


def render_pr_number(ctx: ColumnContext) -> ColumnOutput:
    pr = ctx.pr_number
    if pr is not None:
        return [(f" PR#{pr}", f"bold cyan{ctx.bg}")]
    return None


def render_git_diff_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.git_diff_stats:
        files, ins, dels = ctx.git_diff_stats
        return f"Δ{files} files +{format_line_count(ins)} -{format_line_count(dels)}"
    return None


def render_repo_name_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.all_names_match_repos:
        return None
    return f"{ctx.repo_name}{ctx.worktree_label}"


render_branch_plain = _make_simple_render_plain("branch")
