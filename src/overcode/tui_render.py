"""
Pure render functions for TUI components.

These functions are extracted from TUI widgets to enable unit testing
without requiring the full Textual framework.

All functions are pure - they take data as input and return Rich Text objects.
No side effects, no external dependencies.
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple
from rich.text import Text

from .tui_helpers import (
    format_interval,
    format_duration,
    format_tokens,
    format_cost,
    format_line_count,
    get_status_symbol,
    get_daemon_status_style,
    calculate_uptime,
    get_current_state_times,
)
from .settings import DAEMON_VERSION


def render_daemon_monitor_section(
    monitor_state,  # MonitorDaemonState or None
    is_stale: bool,
) -> Text:
    """Render the Monitor Daemon section of the status bar.

    Args:
        monitor_state: Monitor daemon state object or None
        is_stale: Whether the state is considered stale

    Returns:
        Rich Text for the monitor section
    """
    content = Text()
    content.append("Monitor: ", style="bold")

    monitor_running = monitor_state is not None and not is_stale

    if monitor_running:
        symbol, style = get_daemon_status_style(monitor_state.status)
        content.append(f"{symbol} ", style=style)
        content.append(f"#{monitor_state.loop_count}", style="cyan")
        content.append(f" @{format_interval(monitor_state.current_interval)}", style="dim")

        # Version mismatch warning
        if monitor_state.daemon_version != DAEMON_VERSION:
            content.append(
                f" ⚠v{monitor_state.daemon_version}→{DAEMON_VERSION}",
                style="bold yellow"
            )
    else:
        content.append("○ ", style="red")
        content.append("stopped", style="red")

    return content


def render_supervisor_section(
    supervisor_running: bool,
    monitor_state,  # MonitorDaemonState or None
    is_monitor_running: bool,
) -> Text:
    """Render the Supervisor Daemon section of the status bar.

    Args:
        supervisor_running: Whether supervisor daemon is running
        monitor_state: Monitor daemon state (for supervisor claude info)
        is_monitor_running: Whether monitor daemon is running

    Returns:
        Rich Text for the supervisor section
    """
    content = Text()
    content.append("Supervisor: ", style="bold")

    if supervisor_running:
        content.append("● ", style="green")

        if is_monitor_running and monitor_state and monitor_state.supervisor_claude_running:
            # Calculate current run duration
            run_duration = ""
            if monitor_state.supervisor_claude_started_at:
                try:
                    started = datetime.fromisoformat(monitor_state.supervisor_claude_started_at)
                    elapsed = (datetime.now() - started).total_seconds()
                    run_duration = format_duration(elapsed)
                except (ValueError, TypeError):
                    run_duration = "?"
            content.append(f"🤖 RUNNING {run_duration}", style="bold yellow")

        elif is_monitor_running and monitor_state and monitor_state.total_supervisions > 0:
            content.append(f"sup:{monitor_state.total_supervisions}", style="magenta")
            if monitor_state.supervisor_tokens > 0:
                content.append(f" {format_tokens(monitor_state.supervisor_tokens)}", style="blue")
            if monitor_state.supervisor_claude_total_run_seconds > 0:
                total_run = format_duration(monitor_state.supervisor_claude_total_run_seconds)
                content.append(f" ⏱{total_run}", style="dim")
        else:
            content.append("ready", style="green")
    else:
        content.append("○ ", style="red")
        content.append("stopped", style="red")

    return content


def render_ai_summarizer_section(
    summarizer_available: bool,
    summarizer_enabled: bool,
    summarizer_calls: int,
) -> Text:
    """Render the AI Summarizer section of the status bar.

    Args:
        summarizer_available: Whether API key is available
        summarizer_enabled: Whether summarizer is enabled
        summarizer_calls: Number of API calls made

    Returns:
        Rich Text for the AI section
    """
    content = Text()
    content.append("AI: ", style="bold")

    if summarizer_available:
        if summarizer_enabled:
            content.append("● ", style="green")
            if summarizer_calls > 0:
                content.append(f"{summarizer_calls}", style="cyan")
            else:
                content.append("on", style="green")
        else:
            content.append("○ ", style="dim")
            content.append("off", style="dim")
    else:
        content.append("○ ", style="red")
        content.append("n/a", style="red dim")

    return content


def render_spin_stats(
    sessions: List,  # List of SessionDaemonState
    asleep_session_ids: set,
    show_cost: bool = False,
) -> Text:
    """Render spin rate statistics.

    Args:
        sessions: List of session daemon states
        asleep_session_ids: Set of session IDs that are asleep
        show_cost: Show $ cost instead of token counts

    Returns:
        Rich Text for spin stats section
    """
    content = Text()

    # Filter out sleeping agents
    active_sessions = [s for s in sessions if s.session_id not in asleep_session_ids]
    sleeping_count = len(sessions) - len(active_sessions)

    total_agents = len(active_sessions)
    green_now = sum(1 for s in active_sessions if s.current_status == "running")

    # Calculate mean spin rate
    mean_spin = 0.0
    for s in active_sessions:
        total_time = s.green_time_seconds + s.non_green_time_seconds
        if total_time > 0:
            mean_spin += s.green_time_seconds / total_time

    content.append("Spin: ", style="bold")
    content.append(f"{green_now}", style="bold green" if green_now > 0 else "dim")
    content.append(f"/{total_agents}", style="dim")

    if sleeping_count > 0:
        content.append(f" 💤{sleeping_count}", style="dim")

    if mean_spin > 0:
        content.append(f" μ{mean_spin:.1f}x", style="cyan")

    # Total tokens/cost (include sleeping agents)
    if show_cost:
        total_cost = sum(s.estimated_cost_usd for s in sessions)
        if total_cost > 0:
            content.append(f" {format_cost(total_cost)}", style="orange1")
    else:
        total_tokens = sum(s.input_tokens + s.output_tokens for s in sessions)
        if total_tokens > 0:
            content.append(f" Σ{format_tokens(total_tokens)}", style="orange1")

    return content


def render_presence_indicator(
    presence_state: int,
    idle_seconds: float,
) -> Text:
    """Render presence status indicator.

    Args:
        presence_state: 1=locked, 2=inactive, 3=active
        idle_seconds: Seconds since last activity

    Returns:
        Rich Text for presence indicator
    """
    content = Text()

    state_icons = {1: "🔒", 2: "💤", 3: "👤"}
    state_colors = {1: "red", 2: "yellow", 3: "green"}

    icon = state_icons.get(presence_state, "?")
    color = state_colors.get(presence_state, "dim")

    content.append(f"{icon}", style=color)
    content.append(f" {int(idle_seconds)}s", style="dim")

    return content


def render_session_summary_line(
    name: str,
    detected_status: str,
    expanded: bool,
    summary_detail: str,  # "low", "med", "full"
    start_time: str,
    repo_name: Optional[str],
    branch: Optional[str],
    green_time: float,
    non_green_time: float,
    permissiveness_mode: str,
    state_since: Optional[str],
    local_status_changed_at: Optional[datetime],
    steers_count: int,
    total_tokens: Optional[int],
    current_context_tokens: Optional[int],
    interaction_count: Optional[int],
    median_work_time: float,
    git_diff_stats: Optional[Tuple[int, int, int]],
    is_unvisited_stalled: bool,
    has_focus: bool,
    is_list_mode: bool,
    max_repo_info_width: int = 18,
    show_cost: bool = False,
    estimated_cost_usd: float = 0.0,
) -> Text:
    """Render a single session summary line.

    This is a pure function that builds the Rich Text display for a session.
    All data is passed as parameters, no external dependencies.

    Args:
        name: Session name
        detected_status: Current detected status string
        expanded: Whether session is expanded
        summary_detail: Detail level
        start_time: ISO timestamp of session start
        repo_name: Repository name
        branch: Git branch
        green_time: Total green (running) time in seconds
        non_green_time: Total non-green time in seconds
        permissiveness_mode: "normal", "permissive", or "bypass"
        state_since: ISO timestamp of current state start
        local_status_changed_at: Local datetime when status changed
        steers_count: Number of robot supervisions
        total_tokens: Total tokens used (or None)
        current_context_tokens: Current context window usage (or None)
        interaction_count: Total interactions (or None)
        median_work_time: Median autonomous work time
        git_diff_stats: Tuple of (files, insertions, deletions) or None
        is_unvisited_stalled: Whether this is an unvisited stalled agent
        has_focus: Whether this widget has focus
        is_list_mode: Whether in list view mode
        max_repo_info_width: Width for repo info column
        show_cost: Show $ cost instead of token counts
        estimated_cost_usd: Estimated cost in USD

    Returns:
        Rich Text object for the summary line
    """
    bg = " on #0d2137"

    # Expansion indicator
    expand_icon = "▼" if expanded else "▶"

    # Calculate values
    uptime = calculate_uptime(start_time)
    repo_info = f"{repo_name or 'n/a'}:{branch or 'n/a'}"

    # Status indicator
    status_symbol, base_color = get_status_symbol(detected_status)
    status_color = f"bold {base_color}{bg}"

    # Permissiveness emoji
    perm_emojis = {"bypass": "🔥", "permissive": "🏃"}
    perm_emoji = perm_emojis.get(permissiveness_mode, "👮")

    content = Text()

    # Name width based on detail level
    name_widths = {"low": 24, "med": 20, "full": 16}
    name_width = name_widths.get(summary_detail, 20)
    display_name = name[:name_width].ljust(name_width)

    # Status symbol
    content.append(f"{status_symbol} ", style=status_color)

    # Stalled indicator
    if is_unvisited_stalled:
        content.append("🔔", style=f"bold blink red{bg}")
    else:
        content.append("  ", style=f"dim{bg}")

    # Time in current state
    state_start = local_status_changed_at
    if state_since:
        try:
            daemon_state_start = datetime.fromisoformat(state_since)
            if state_start is None or daemon_state_start > state_start:
                state_start = daemon_state_start
        except (ValueError, TypeError):
            pass

    if state_start:
        elapsed = (datetime.now() - state_start).total_seconds()
        content.append(f"{format_duration(elapsed):>5} ", style=status_color)
    else:
        content.append("    - ", style=f"dim{bg}")

    # Focus/expand indicator
    if is_list_mode:
        if has_focus:
            content.append("→ ", style=status_color)
        else:
            content.append("  ", style=status_color)
    else:
        content.append(f"{expand_icon} ", style=status_color)

    content.append(f"{display_name}", style=f"bold cyan{bg}")

    # Full detail: repo:branch
    if summary_detail == "full":
        content.append(f" {repo_info:<{max_repo_info_width}} ", style=f"bold dim{bg}")

    # Med/Full: uptime, times
    if summary_detail in ("med", "full"):
        content.append(f" ↑{uptime:>5}", style=f"bold white{bg}")
        content.append(f" ▶{format_duration(green_time):>5}", style=f"bold green{bg}")
        content.append(f" ⏸{format_duration(non_green_time):>5}", style=f"bold red{bg}")

        if summary_detail == "full":
            total_time = green_time + non_green_time
            pct = (green_time / total_time * 100) if total_time > 0 else 0
            pct_style = f"bold green{bg}" if pct >= 50 else f"bold red{bg}"
            content.append(f" {pct:>3.0f}%", style=pct_style)

    # Token usage or cost
    if total_tokens is not None:
        if show_cost:
            content.append(f" {format_cost(estimated_cost_usd):>7}", style=f"bold orange1{bg}")
        else:
            content.append(f" Σ{format_tokens(total_tokens):>6}", style=f"bold orange1{bg}")
        if current_context_tokens and current_context_tokens > 0:
            max_context = 200_000
            ctx_pct = min(100, current_context_tokens / max_context * 100)
            content.append(f" c@{ctx_pct:>3.0f}%", style=f"bold orange1{bg}")
        else:
            content.append(" c@  -%", style=f"dim orange1{bg}")
    else:
        content.append("      - c@  -%", style=f"dim orange1{bg}")

    # Git diff stats
    if git_diff_stats:
        files, ins, dels = git_diff_stats
        if summary_detail == "full":
            content.append(f" Δ{files:>2}", style=f"bold magenta{bg}")
            content.append(f" +{format_line_count(ins):>4}", style=f"bold green{bg}")
            content.append(f" -{format_line_count(dels):>4}", style=f"bold red{bg}")
        else:
            style = f"bold magenta{bg}" if files > 0 else f"dim{bg}"
            content.append(f" Δ{files:>2}", style=style)
    else:
        if summary_detail == "full":
            content.append("  Δ-  +   -  -  ", style=f"dim{bg}")
        else:
            content.append("  Δ-", style=f"dim{bg}")

    # Med/Full: median work time
    if summary_detail in ("med", "full"):
        work_str = format_duration(median_work_time) if median_work_time > 0 else "0s"
        content.append(f" ⏱{work_str:>5}", style=f"bold blue{bg}")

    # Permission mode, human/robot counts
    content.append(f" {perm_emoji}", style=f"bold white{bg}")

    if interaction_count is not None:
        human_count = max(0, interaction_count - steers_count)
        content.append(f" 👤{human_count:>3}", style=f"bold yellow{bg}")
    else:
        content.append(" 👤  -", style=f"dim yellow{bg}")

    content.append(f" 🤖{steers_count:>3}", style=f"bold cyan{bg}")

    return content
