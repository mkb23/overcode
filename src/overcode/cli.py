"""
CLI interface for Overcode using Typer.
"""

import sys
from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich import print as rprint
from rich.console import Console

from .launcher import ClaudeLauncher

# Main app
app = typer.Typer(
    name="overcode",
    help="Manage and supervise Claude Code agents",
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
)


# Monitor daemon subcommand group
monitor_daemon_app = typer.Typer(
    name="monitor-daemon",
    help="Manage the Monitor Daemon (metrics/state tracking)",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(monitor_daemon_app, name="monitor-daemon")

# Supervisor daemon subcommand group
supervisor_daemon_app = typer.Typer(
    name="supervisor-daemon",
    help="Manage the Supervisor Daemon (Claude orchestration)",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(supervisor_daemon_app, name="supervisor-daemon")

# Hooks subcommand group
hooks_app = typer.Typer(
    name="hooks",
    help="Manage Claude Code hook integration.",
    no_args_is_help=True,
)
app.add_typer(hooks_app, name="hooks")

# Budget subcommand group (#244)
budget_app = typer.Typer(
    name="budget",
    help="Manage agent cost budgets.",
    no_args_is_help=True,
)
app.add_typer(budget_app, name="budget")

# Config subcommand group
config_app = typer.Typer(
    name="config",
    help="Manage configuration",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(config_app, name="config")

# Console for rich output
console = Console()

# Global session option (hidden advanced usage)
SessionOption = Annotated[
    str,
    typer.Option(
        "--session",
        hidden=True,
        help="Tmux session name for agents",
    ),
]


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """Launch the TUI monitor when no command is given."""
    if ctx.invoked_subcommand is None:
        from .tui import run_tui

        run_tui("agents")


# =============================================================================
# Agent Commands
# =============================================================================


@app.command()
def launch(
    name: Annotated[str, typer.Option("--name", "-n", help="Name for the agent")],
    directory: Annotated[
        Optional[str], typer.Option("--directory", "-d", help="Working directory")
    ] = None,
    prompt: Annotated[
        Optional[str], typer.Option("--prompt", "-p", help="Initial prompt to send")
    ] = None,
    skip_permissions: Annotated[
        bool,
        typer.Option(
            "--skip-permissions",
            help="Auto-deny permission prompts (--permission-mode dontAsk)",
        ),
    ] = False,
    bypass_permissions: Annotated[
        bool,
        typer.Option(
            "--bypass-permissions",
            help="Bypass all permission checks (--dangerously-skip-permissions)",
        ),
    ] = False,
    parent: Annotated[
        Optional[str],
        typer.Option("--parent", help="Parent agent name for hierarchy (#244)"),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Stream child output and block until done (#244)"),
    ] = False,
    session: SessionOption = "agents",
):
    """Launch a new Claude agent."""
    import os

    # Default to current directory if not specified
    working_dir = directory if directory else os.getcwd()

    launcher = ClaudeLauncher(session)

    result = launcher.launch(
        name=name,
        start_directory=working_dir,
        initial_prompt=prompt,
        skip_permissions=skip_permissions,
        dangerously_skip_permissions=bypass_permissions,
        parent_name=parent,
    )

    if result:
        rprint(f"\n[green]✓[/green] Agent '[bold]{name}[/bold]' launched")
        if result.parent_session_id:
            rprint(f"  Parent: {parent or os.environ.get('OVERCODE_SESSION_NAME', '?')}")
        if prompt:
            rprint("  Initial prompt sent")

        if follow:
            from .follow_mode import follow_agent
            exit_code = follow_agent(name, session)
            raise typer.Exit(code=exit_code)

        rprint("\nTo view: [bold]overcode attach[/bold]")


@app.command("list")
def list_agents(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name to filter (show agent + descendants)")
    ] = None,
    show_done: Annotated[
        bool, typer.Option("--show-done", help="Include 'done' child agents (#244)")
    ] = False,
    session: SessionOption = "agents",
):
    """List running agents with status.

    With no arguments, shows all agents with depth-based indentation.
    With a name, shows that agent + all its descendants.
    """
    from .tui_helpers import (
        calculate_uptime, format_duration, format_tokens,
        get_current_state_times, get_status_symbol
    )
    from .monitor_daemon_state import get_monitor_daemon_state

    launcher = ClaudeLauncher(session)
    sessions = launcher.list_sessions()

    if not sessions:
        rprint("[dim]No running agents[/dim]")
        return

    # Filter to specific agent + descendants if name given (#244)
    if name:
        root = launcher.sessions.get_session_by_name(name)
        if not root:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        descendants = launcher.sessions.get_descendants(root.id)
        allowed_ids = {root.id} | {d.id for d in descendants}
        sessions = [s for s in sessions if s.id in allowed_ids]

    # Filter out "done" agents unless --show-done (#244)
    if not show_done:
        sessions = [s for s in sessions if s.status != "done"]

    # Prefer daemon state for status/activity (single source of truth)
    daemon_state = get_monitor_daemon_state(session)
    use_daemon = daemon_state is not None and not daemon_state.is_stale()

    # Only create detector as fallback when daemon isn't running
    detector = None
    if not use_daemon:
        from .status_detector_factory import StatusDetectorDispatcher
        detector = StatusDetectorDispatcher(session)

    terminated_count = 0

    for sess in sessions:
        if sess.status == "terminated":
            status = "terminated"
            activity = "(tmux window no longer exists)"
            terminated_count += 1
        elif sess.status == "done":
            status = "done"
            activity = "(completed)"
        elif use_daemon:
            ds = daemon_state.get_session_by_name(sess.name)
            if ds:
                status = ds.current_status
                activity = ds.current_activity
            else:
                # Session not yet in daemon state — detect directly
                if detector is None:
                    from .status_detector_factory import StatusDetectorDispatcher
                    detector = StatusDetectorDispatcher(session)
                status, activity, _ = detector.detect_status(sess)
        else:
            status, activity, _ = detector.detect_status(sess)

        if sess.is_asleep:
            status = "asleep"

        symbol, _ = get_status_symbol(status)

        # Calculate uptime using shared helper
        uptime = calculate_uptime(sess.start_time) if sess.start_time else "?"

        # Get state times using shared helper
        green_time, non_green_time, sleep_time = get_current_state_times(sess.stats, is_asleep=sess.is_asleep)

        # Stats from session manager (already synced by daemon)
        stats = sess.stats
        if stats.interaction_count > 0:
            stats_display = f"{stats.interaction_count:>2}i {format_tokens(stats.input_tokens + stats.output_tokens):>5}"
        else:
            stats_display = " -i     -"

        # Build time display - show sleep time if agent has slept
        time_display = f"▶{format_duration(green_time):>5} ⏸{format_duration(non_green_time):>5}"
        if sleep_time > 0:
            time_display += f" 💤{format_duration(sleep_time):>5}"

        # Compute depth for indentation (#244)
        depth = launcher.sessions.compute_depth(sess)
        indent = "  " * depth

        print(
            f"{symbol} {indent}{sess.name:<{16 - len(indent)}} ↑{uptime:>5}  "
            f"{time_display}  "
            f"{stats_display}  {activity[:50]}"
        )

    if terminated_count > 0:
        rprint(f"\n[dim]{terminated_count} terminated session(s). Run 'overcode cleanup' to remove.[/dim]")


@app.command()
def attach(session: SessionOption = "agents"):
    """Attach to the tmux session to view agents."""
    launcher = ClaudeLauncher(session)
    rprint("[dim]Attaching to overcode...[/dim]")
    rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    launcher.attach()


@app.command()
def kill(
    name: Annotated[str, typer.Argument(help="Name of agent to kill")],
    no_cascade: Annotated[
        bool,
        typer.Option("--no-cascade", help="Don't kill child agents (orphan them instead)"),
    ] = False,
    session: SessionOption = "agents",
):
    """Kill a running agent.

    By default, also kills all descendant (child) agents.
    Use --no-cascade to only kill the named agent and orphan its children.
    """
    launcher = ClaudeLauncher(session)
    launcher.kill_session(name, cascade=not no_cascade)


@app.command()
def follow(
    name: Annotated[str, typer.Argument(help="Name of agent to follow")],
    session: SessionOption = "agents",
):
    """Follow an already-running agent, streaming its output (#244).

    Blocks until the agent reaches Stop. Press Ctrl-C to stop following
    (the agent keeps running in tmux).

    Examples:
        overcode follow my-child-agent
    """
    from .follow_mode import follow_agent as _follow_agent

    exit_code = _follow_agent(name, session)
    raise typer.Exit(code=exit_code)


@app.command()
def cleanup(
    done: Annotated[
        bool, typer.Option("--done", help="Also archive 'done' child agents (#244)")
    ] = False,
    session: SessionOption = "agents",
):
    """Remove terminated sessions from tracking.

    Terminated sessions are those whose tmux window no longer exists
    (e.g., after a machine reboot). Use 'overcode list' to see them.

    Use --done to also archive done child agents (kill tmux window, move to archive).
    """
    launcher = ClaudeLauncher(session)
    count = launcher.cleanup_terminated_sessions()

    # Also clean up done agents if requested (#244)
    done_count = 0
    if done:
        all_sessions = launcher.sessions.list_sessions()
        done_sessions = [s for s in all_sessions if s.status == "done"]
        for sess in done_sessions:
            launcher._kill_single_session(sess)
            done_count += 1

    total = count + done_count
    if total > 0:
        parts = []
        if count > 0:
            parts.append(f"{count} terminated")
        if done_count > 0:
            parts.append(f"{done_count} done")
        rprint(f"[green]✓ Cleaned up {' + '.join(parts)} session(s)[/green]")
    else:
        rprint("[dim]No sessions to clean up[/dim]")


@app.command(name="set-value")
def set_value(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    value: Annotated[int, typer.Argument(help="Priority value (default 1000, higher = more important)")],
    session: SessionOption = "agents",
):
    """Set agent priority value for sorting (#61).

    Higher values indicate higher priority. Default is 1000.

    Examples:
        overcode set-value my-agent 2000    # High priority
        overcode set-value my-agent 500     # Low priority
        overcode set-value my-agent 1000    # Reset to default
    """
    from .session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    manager.set_agent_value(agent.id, value)
    rprint(f"[green]✓ Set {name} value to {value}[/green]")


@app.command(name="set-budget", hidden=True)
def set_budget(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    budget: Annotated[float, typer.Argument(help="Budget in USD (0 to clear)")],
    session: SessionOption = "agents",
):
    """[Deprecated] Use 'overcode budget set' instead.

    Set cost budget for an agent (#173).
    """
    from .session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if budget < 0:
        rprint("[red]Error: Budget cannot be negative[/red]")
        raise typer.Exit(code=1)

    manager.set_cost_budget(agent.id, budget)
    if budget > 0:
        rprint(f"[green]✓ Set {name} budget to ${budget:.2f}[/green]")
    else:
        rprint(f"[green]✓ Cleared budget for {name}[/green]")


# =============================================================================
# Budget Commands (#244)
# =============================================================================


@budget_app.command("set")
def budget_set(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    amount: Annotated[float, typer.Argument(help="Budget in USD (0 to clear)")],
    session: SessionOption = "agents",
):
    """Set cost budget for an agent.

    When an agent's estimated cost reaches the budget, heartbeats are
    disabled and supervision is skipped.

    Examples:
        overcode budget set my-agent 5.00    # $5 budget
        overcode budget set my-agent 0       # Clear budget
    """
    from .session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if amount < 0:
        rprint("[red]Error: Budget cannot be negative[/red]")
        raise typer.Exit(code=1)

    manager.set_cost_budget(agent.id, amount)
    if amount > 0:
        rprint(f"[green]✓ Set {name} budget to ${amount:.2f}[/green]")
    else:
        rprint(f"[green]✓ Cleared budget for {name}[/green]")


@budget_app.command("transfer")
def budget_transfer(
    source: Annotated[str, typer.Argument(help="Source agent name (must be ancestor)")],
    target: Annotated[str, typer.Argument(help="Target agent name")],
    amount: Annotated[float, typer.Argument(help="Amount in USD to transfer")],
    session: SessionOption = "agents",
):
    """Transfer budget from parent to child agent (#244).

    Source must be an ancestor of target in the agent hierarchy.
    If source has unlimited budget (0), the target's budget is simply set.

    Examples:
        overcode budget transfer parent-agent child-agent 2.00
    """
    from .session_manager import SessionManager

    manager = SessionManager()

    source_agent = manager.get_session_by_name(source)
    if not source_agent:
        rprint(f"[red]Error: Source agent '{source}' not found[/red]")
        raise typer.Exit(code=1)

    target_agent = manager.get_session_by_name(target)
    if not target_agent:
        rprint(f"[red]Error: Target agent '{target}' not found[/red]")
        raise typer.Exit(code=1)

    if amount <= 0:
        rprint("[red]Error: Transfer amount must be positive[/red]")
        raise typer.Exit(code=1)

    if not manager.is_ancestor(source_agent.id, target_agent.id):
        rprint(f"[red]Error: '{source}' is not an ancestor of '{target}'[/red]")
        raise typer.Exit(code=1)

    success = manager.transfer_budget(source_agent.id, target_agent.id, amount)
    if success:
        rprint(f"[green]✓ Transferred ${amount:.2f} from {source} to {target}[/green]")
    else:
        rprint(f"[red]Transfer failed: insufficient budget on '{source}'[/red]")
        raise typer.Exit(code=1)


@budget_app.command("show")
def budget_show(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name (omit for all)")
    ] = None,
    session: SessionOption = "agents",
):
    """Show budget status for agents.

    Shows per-agent budget, spent, remaining, and % used.
    For parents, includes subtree total spend.

    Examples:
        overcode budget show              # All agents
        overcode budget show my-agent     # Specific agent
    """
    from .session_manager import SessionManager
    from .tui_helpers import format_duration

    manager = SessionManager()

    if name:
        agents = []
        agent = manager.get_session_by_name(name)
        if not agent:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        agents = [agent]
    else:
        agents = manager.list_sessions()
        if not agents:
            rprint("[dim]No running agents[/dim]")
            return

    for agent in agents:
        budget = agent.cost_budget_usd
        spent = agent.stats.estimated_cost_usd
        budget_str = f"${budget:.2f}" if budget > 0 else "unlimited"
        spent_str = f"${spent:.4f}"

        if budget > 0:
            remaining = max(0, budget - spent)
            pct = (spent / budget * 100) if budget > 0 else 0
            status = f"${remaining:.2f} remaining ({pct:.0f}% used)"
        else:
            status = "no limit"

        # Check for children's spend
        children = manager.get_descendants(agent.id)
        subtree_spend = spent + sum(c.stats.estimated_cost_usd for c in children)

        depth = manager.compute_depth(agent)
        indent = "  " * depth
        line = f"  {indent}{agent.name:<16} budget={budget_str:<10} spent={spent_str:<10} {status}"
        if children:
            line += f"  (subtree: ${subtree_spend:.4f})"
        print(line)


@app.command()
def annotate(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    text: Annotated[
        Optional[List[str]], typer.Argument(help="Annotation text (omit to clear)")
    ] = None,
    session: SessionOption = "agents",
):
    """Set or clear a human annotation on an agent (#223).

    Allows programmatic annotation of agents so scripts and other tools
    can communicate status to the overcode TUI.

    Examples:
        overcode annotate my-agent "Working on auth module"
        overcode annotate my-agent Building the API layer
        overcode annotate my-agent                           # Clear annotation
    """
    from .session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    annotation = " ".join(text) if text else ""
    manager.set_human_annotation(agent.id, annotation)
    if annotation:
        rprint(f"[green]✓ Annotation set for {name}:[/green] {annotation}")
    else:
        rprint(f"[green]✓ Annotation cleared for {name}[/green]")


@app.command()
def send(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    text: Annotated[
        Optional[List[str]], typer.Argument(help="Text to send (or special key: enter, escape)")
    ] = None,
    no_enter: Annotated[
        bool, typer.Option("--no-enter", help="Don't press Enter after text")
    ] = False,
    session: SessionOption = "agents",
):
    """
    Send input to an agent.

    Special keys: enter, escape, tab, up, down, left, right

    Examples:
        overcode send my-agent "yes"           # Send "yes" + Enter
        overcode send my-agent enter           # Just press Enter (approve)
        overcode send my-agent escape          # Press Escape (reject)
        overcode send my-agent --no-enter "y"  # Send "y" without Enter
    """
    launcher = ClaudeLauncher(session)

    # Join all text parts if multiple were given
    text_str = " ".join(text) if text else ""
    enter = not no_enter

    if launcher.send_to_session(name, text_str, enter=enter):
        if text_str.lower() in ("enter", "escape", "esc"):
            rprint(f"[green]✓[/green] Sent {text_str.upper()} to '[bold]{name}[/bold]'")
        elif enter:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent to '[bold]{name}[/bold]': {display}")
        else:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent (no enter) to '[bold]{name}[/bold]': {display}")
    else:
        rprint(f"[red]✗[/red] Failed to send to '[bold]{name}[/bold]'")
        raise typer.Exit(1)


@app.command()
def show(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Number of lines to show")
    ] = 50,
    no_stats: Annotated[
        bool, typer.Option("--no-stats", help="Skip stats, show only pane output")
    ] = False,
    stats_only: Annotated[
        bool, typer.Option("--stats-only", "-s", help="Show only stats, no pane output")
    ] = False,
    session: SessionOption = "agents",
):
    """Show agent details and recent output."""
    from .history_reader import get_session_stats
    from .status_patterns import extract_background_bash_count, extract_live_subagent_count, strip_ansi
    from .tui_helpers import get_git_diff_stats
    from .summary_columns import build_cli_context, render_cli_stats
    from .monitor_daemon_state import get_monitor_daemon_state

    launcher = ClaudeLauncher(session)

    # Get the Session object
    sess = launcher.sessions.get_session_by_name(name)
    if sess is None:
        rprint(f"[red]✗[/red] Agent '[bold]{name}[/bold]' not found")
        raise typer.Exit(1)

    # Read daemon state for status/activity (single source of truth)
    daemon_state = get_monitor_daemon_state(session)
    daemon_session = None
    if daemon_state and not daemon_state.is_stale():
        daemon_session = daemon_state.get_session_by_name(name)

    # Get status/activity from daemon state, falling back to detection
    pane_content_raw = ""
    if sess.status == "terminated":
        status = "terminated"
        activity = "(tmux window no longer exists)"
    elif daemon_session:
        status = daemon_session.current_status
        activity = daemon_session.current_activity
    else:
        # Daemon not running — fall back to direct detection
        from .status_detector_factory import create_status_detector
        detector = create_status_detector(
            session,
            strategy="hooks" if sess.hook_status_detection else "polling",
        )
        status, activity, pane_content_raw = detector.detect_status(sess)

    if sess.is_asleep:
        status = "asleep"

    # Capture pane content separately if needed for display or stats parsing
    need_pane = (not stats_only and lines > 0) or not no_stats
    if need_pane and not pane_content_raw and sess.status != "terminated":
        from .status_detector_factory import StatusDetectorDispatcher
        dispatcher = StatusDetectorDispatcher(session)
        pane_content_raw = dispatcher.get_pane_content(sess.tmux_window, num_lines=max(lines, 50))

    if not no_stats:
        # Gather all stats
        bg_bash_count = extract_background_bash_count(pane_content_raw) if pane_content_raw else 0
        live_sub_count = extract_live_subagent_count(pane_content_raw) if pane_content_raw else 0

        claude_stats = None
        try:
            claude_stats = get_session_stats(sess)
        except Exception:
            pass

        git_diff = None
        try:
            if sess.start_directory:
                git_diff = get_git_diff_stats(sess.start_directory)
        except Exception:
            pass

        # AI summaries from daemon state
        ai_short = ""
        ai_context = ""
        if daemon_session:
            ai_short = daemon_session.activity_summary or ""
            ai_context = daemon_session.activity_summary_context or ""

        # Build context and render via column system
        any_has_budget = sess.cost_budget_usd > 0
        ctx = build_cli_context(
            session=sess,
            stats=sess.stats,
            claude_stats=claude_stats,
            git_diff_stats=git_diff,
            status=status,
            bg_bash_count=bg_bash_count,
            live_sub_count=live_sub_count,
            any_has_budget=any_has_budget,
        )

        print(f"=== {name} ===")
        label_width = max(len(label) for label, _ in render_cli_stats(ctx)) + 1
        for label, value in render_cli_stats(ctx):
            print(f"{label + ':':<{label_width + 1}} {value}")

        # AI summaries (not a column — comes from daemon state)
        if ai_short:
            print(f"{'AI:':<{label_width + 1}} {ai_short}")
        if ai_context:
            print(f"{'Context:':<{label_width + 1}} {ai_context}")

        # Activity from status detector (not a column — transient)
        if activity:
            print(f"{'Activity:':<{label_width + 1}} {activity[:100]}")

        print()

    # Pane output section (skip if --stats-only or --lines 0)
    if not stats_only and lines > 0:
        if pane_content_raw:
            clean_content = strip_ansi(pane_content_raw)
            content_lines = clean_content.rstrip().split('\n')
            display_lines = content_lines[-lines:]
            print(f"=== {name} (last {lines} lines) ===")
            print('\n'.join(display_lines))
            print(f"=== end {name} ===")
        else:
            # Fallback for terminated sessions
            output = launcher.get_session_output(name, lines=lines)
            if output is not None:
                print(f"=== {name} (last {lines} lines) ===")
                print(output)
                print(f"=== end {name} ===")
            else:
                rprint(f"[dim]No pane output available[/dim]")


# =============================================================================
# Hooks Commands
# =============================================================================


@hooks_app.command("install")
def hooks_install(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Install to project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """Install all overcode hooks into Claude Code settings.

    Installs hooks for: UserPromptSubmit, PostToolUse, Stop,
    PermissionRequest, SessionEnd. All use the unified 'overcode hook-handler'.
    """
    from .claude_config import ClaudeConfigEditor
    from .hook_handler import OVERCODE_HOOKS

    if project:
        editor = ClaudeConfigEditor.project_level()
        level = "project"
    else:
        editor = ClaudeConfigEditor.user_level()
        level = "user"

    try:
        settings = editor.load()
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Install all overcode hooks (idempotent)
    installed = 0
    already = 0
    for event, command in OVERCODE_HOOKS:
        if editor.add_hook(event, command):
            installed += 1
        else:
            already += 1

    if installed > 0:
        events = ", ".join(event for event, _ in OVERCODE_HOOKS)
        rprint(f"[green]\u2713[/green] Installed {installed} hook(s) in {level} settings")
        rprint(f"  [dim]{editor.path}[/dim]")
        rprint(f"\n  Events: {events}")
        rprint(f"  All hooks run 'overcode hook-handler' (reads event from stdin).")
    elif already == len(OVERCODE_HOOKS):
        rprint(f"[green]\u2713[/green] All {already} hooks already installed in {level} settings")


@hooks_app.command("uninstall")
def hooks_uninstall(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Uninstall from project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """Remove all overcode hooks from Claude Code settings."""
    from .claude_config import ClaudeConfigEditor
    from .hook_handler import OVERCODE_HOOKS

    if project:
        editor = ClaudeConfigEditor.project_level()
        level = "project"
    else:
        editor = ClaudeConfigEditor.user_level()
        level = "user"

    try:
        editor.load()
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    removed = 0
    for event, command in OVERCODE_HOOKS:
        if editor.remove_hook(event, command):
            removed += 1

    if removed > 0:
        rprint(f"[green]\u2713[/green] Removed {removed} hook(s) from {level} settings")
    else:
        rprint(f"[dim]No overcode hooks found in {level} settings[/dim]")


@hooks_app.command("status")
def hooks_status():
    """Show which overcode hooks are installed."""
    from .claude_config import ClaudeConfigEditor
    from .hook_handler import OVERCODE_HOOKS

    for level_name, editor in [
        ("User-level", ClaudeConfigEditor.user_level()),
        ("Project-level", ClaudeConfigEditor.project_level()),
    ]:
        try:
            editor.load()
        except ValueError:
            rprint(f"\n{level_name} ({editor.path}):")
            rprint(f"  [red](invalid JSON)[/red]")
            continue

        if not editor.path.exists():
            rprint(f"\n{level_name} ({editor.path}):")
            rprint(f"  [dim](no settings file)[/dim]")
            continue

        rprint(f"\n{level_name} ({editor.path}):")

        for event, command in OVERCODE_HOOKS:
            if editor.has_hook(event, command):
                rprint(f"  {event:<20} {command}  [green]\u2713[/green]")
            else:
                rprint(f"  {event:<20} [dim]not installed[/dim]")


@app.command("hook-handler", hidden=True)
def hook_handler_cmd():
    """Handle Claude Code hook events (internal).

    Called by Claude Code hooks, not by users directly.
    Reads event JSON from stdin, writes state for status detection,
    and outputs time-context for UserPromptSubmit events.
    """
    from .hook_handler import handle_hook_event

    handle_hook_event()


@app.command()
def instruct(
    name: Annotated[
        Optional[str], typer.Argument(help="Name of agent")
    ] = None,
    instructions: Annotated[
        Optional[List[str]],
        typer.Argument(help="Instructions or preset name (e.g., DO_NOTHING, STANDARD, CODING)"),
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", "-c", help="Clear standing instructions")
    ] = False,
    list_presets: Annotated[
        bool, typer.Option("--list", "-l", help="List available presets")
    ] = False,
    session: SessionOption = "agents",
):
    """Set standing instructions for an agent.

    Use a preset name (DO_NOTHING, STANDARD, CODING, etc.) or provide custom instructions.
    Use --list to see all available presets.
    """
    from .session_manager import SessionManager
    from .standing_instructions import resolve_instructions, load_presets

    if list_presets:
        presets_dict = load_presets()
        rprint("\n[bold]Standing Instruction Presets:[/bold]\n")
        for preset_name in sorted(presets_dict.keys(), key=lambda x: (x != "DO_NOTHING", x)):
            preset = presets_dict[preset_name]
            rprint(f"  [cyan]{preset_name:12}[/cyan] {preset.description}")
        rprint("\n[dim]Usage: overcode instruct <agent> <PRESET>[/dim]")
        rprint("[dim]       overcode instruct <agent> \"custom instructions\"[/dim]")
        rprint("[dim]Config: ~/.overcode/presets.json[/dim]\n")
        return

    if not name:
        rprint("[red]Error:[/red] Agent name required")
        rprint("[dim]Usage: overcode instruct <agent> <PRESET or instructions>[/dim]")
        raise typer.Exit(1)

    sessions = SessionManager()
    sess = sessions.get_session_by_name(name)

    if sess is None:
        rprint(f"[red]✗[/red] Agent '[bold]{name}[/bold]' not found")
        raise typer.Exit(1)

    instructions_str = " ".join(instructions) if instructions else ""

    if clear:
        sessions.set_standing_instructions(sess.id, "", preset_name=None)
        rprint(f"[green]✓[/green] Cleared standing instructions for '[bold]{name}[/bold]'")
    elif instructions_str:
        # Resolve preset or use as custom instructions
        full_instructions, preset_name = resolve_instructions(instructions_str)
        sessions.set_standing_instructions(sess.id, full_instructions, preset_name=preset_name)

        if preset_name:
            rprint(f"[green]✓[/green] Set '[bold]{name}[/bold]' to [cyan]{preset_name}[/cyan] preset")
            rprint(f"  [dim]{full_instructions[:80]}...[/dim]" if len(full_instructions) > 80 else f"  [dim]{full_instructions}[/dim]")
        else:
            rprint(f"[green]✓[/green] Set standing instructions for '[bold]{name}[/bold]':")
            rprint(f'  "{instructions_str}"')
    else:
        # Show current instructions
        if sess.standing_instructions:
            if sess.standing_instructions_preset:
                rprint(f"Standing instructions for '[bold]{name}[/bold]': [cyan]{sess.standing_instructions_preset}[/cyan] preset")
            else:
                rprint(f"Standing instructions for '[bold]{name}[/bold]':")
            rprint(f'  "{sess.standing_instructions}"')
        else:
            rprint(f"[dim]No standing instructions set for '{name}'[/dim]")
            rprint(f"[dim]Tip: Use 'overcode presets' to see available presets[/dim]")


def _signal_heartbeat_change(session: str) -> None:
    """Wake the monitor daemon so heartbeat status updates immediately (#212)."""
    from .settings import signal_activity
    signal_activity(session)


@app.command()
def heartbeat(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    enable: Annotated[
        bool, typer.Option("--enable", "-e", help="Enable heartbeat")
    ] = False,
    disable: Annotated[
        bool, typer.Option("--disable", "-d", help="Disable heartbeat")
    ] = False,
    pause: Annotated[
        bool, typer.Option("--pause", help="Pause heartbeat (keep config)")
    ] = False,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume paused heartbeat")
    ] = False,
    frequency: Annotated[
        Optional[str], typer.Option("--frequency", "-f", help="Interval (e.g., 300, 5m, 1h)")
    ] = None,
    instruction: Annotated[
        Optional[str], typer.Option("--instruction", "-i", help="Instruction to send")
    ] = None,
    show: Annotated[
        bool, typer.Option("--show", "-s", help="Show current heartbeat config")
    ] = False,
    session: SessionOption = "agents",
):
    """Configure heartbeat for an agent (#171).

    Heartbeat sends a periodic instruction to keep agents active or provide
    regular status updates. The instruction is sent at the configured frequency.

    Examples:
        overcode heartbeat my-agent --show              # Show current config
        overcode heartbeat my-agent -e -f 5m -i "Status check"  # Enable
        overcode heartbeat my-agent --pause             # Temporarily pause
        overcode heartbeat my-agent --resume            # Resume
        overcode heartbeat my-agent --disable           # Disable completely
    """
    from .session_manager import SessionManager
    from .tui_helpers import format_duration

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    # Parse frequency if provided
    freq_seconds = None
    if frequency:
        freq = frequency.strip().lower()
        try:
            if freq.endswith('s'):
                freq_seconds = int(freq[:-1])
            elif freq.endswith('m'):
                freq_seconds = int(freq[:-1]) * 60
            elif freq.endswith('h'):
                freq_seconds = int(freq[:-1]) * 3600
            else:
                freq_seconds = int(freq)
        except ValueError:
            rprint(f"[red]Error: Invalid frequency format '{frequency}'[/red]")
            rprint("[dim]Use: 300, 5m, or 1h[/dim]")
            raise typer.Exit(code=1)

        if freq_seconds < 30:
            rprint("[red]Error: Minimum heartbeat interval is 30 seconds[/red]")
            raise typer.Exit(code=1)

    # Show current config
    if show or (not enable and not disable and not pause and not resume
                and not frequency and not instruction):
        if agent.heartbeat_enabled:
            freq_str = format_duration(agent.heartbeat_frequency_seconds)
            status = "[yellow]paused[/yellow]" if agent.heartbeat_paused else "[green]enabled[/green]"
            rprint(f"Heartbeat for '[bold]{name}[/bold]': {status}")
            rprint(f"  Frequency: {freq_str}")
            rprint(f"  Instruction: {agent.heartbeat_instruction or '(none)'}")
            if agent.last_heartbeat_time:
                rprint(f"  Last sent: {agent.last_heartbeat_time}")
        else:
            rprint(f"Heartbeat for '[bold]{name}[/bold]': [dim]disabled[/dim]")
        return

    # Disable
    if disable:
        manager.update_session(
            agent.id,
            heartbeat_enabled=False,
            heartbeat_paused=False,
            heartbeat_instruction="",
        )
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat disabled for {name}[/green]")
        return

    # Pause
    if pause:
        if not agent.heartbeat_enabled:
            rprint(f"[yellow]Heartbeat is not enabled for {name}[/yellow]")
            return
        manager.update_session(agent.id, heartbeat_paused=True)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat paused for {name}[/green]")
        return

    # Resume
    if resume:
        if not agent.heartbeat_enabled:
            rprint(f"[yellow]Heartbeat is not enabled for {name}[/yellow]")
            return
        manager.update_session(agent.id, heartbeat_paused=False)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat resumed for {name}[/green]")
        return

    # Enable with frequency and instruction
    if enable:
        if not instruction:
            rprint("[red]Error: --instruction required when enabling heartbeat[/red]")
            raise typer.Exit(code=1)

        final_freq = freq_seconds or 300  # Default 5 minutes
        manager.update_session(
            agent.id,
            heartbeat_enabled=True,
            heartbeat_paused=False,
            heartbeat_frequency_seconds=final_freq,
            heartbeat_instruction=instruction,
        )
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat enabled for {name}[/green]")
        rprint(f"  Frequency: {format_duration(final_freq)}")
        rprint(f"  Instruction: {instruction}")
        return

    # Update frequency or instruction without full enable
    updates = {}
    if freq_seconds:
        updates['heartbeat_frequency_seconds'] = freq_seconds
    if instruction:
        updates['heartbeat_instruction'] = instruction

    if updates:
        manager.update_session(agent.id, **updates)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat config updated for {name}[/green]")
        if freq_seconds:
            rprint(f"  Frequency: {format_duration(freq_seconds)}")
        if instruction:
            rprint(f"  Instruction: {instruction}")


# =============================================================================
# Monitoring Commands
# =============================================================================


@app.command()
def monitor(
    session: SessionOption = "agents",
    restart: Annotated[
        bool, typer.Option("--restart", help="Restart the monitor daemon before launching")
    ] = False,
    diagnostics: Annotated[
        bool, typer.Option("--diagnostics", help="Diagnostic mode: disable all auto-refresh timers")
    ] = False,
):
    """Launch the standalone TUI monitor."""
    if restart:
        from .monitor_daemon import stop_monitor_daemon, is_monitor_daemon_running, get_monitor_daemon_pid

        if is_monitor_daemon_running(session):
            pid = get_monitor_daemon_pid(session)
            if stop_monitor_daemon(session):
                rprint(f"[green]✓[/green] Monitor daemon stopped (was PID {pid})")
            else:
                rprint("[red]Failed to stop monitor daemon[/red]")
                raise typer.Exit(1)

    from .tui import run_tui

    run_tui(session, diagnostics=diagnostics)


@app.command()
def supervisor(
    restart: Annotated[
        bool, typer.Option("--restart", help="Restart if already running")
    ] = False,
    session: SessionOption = "agents",
):
    """Launch the TUI monitor with embedded controller Claude."""
    import subprocess
    import os

    if restart:
        rprint("[dim]Killing existing controller session...[/dim]")
        result = subprocess.run(
            ["tmux", "kill-session", "-t", "overcode-controller"],
            capture_output=True,
        )
        if result.returncode == 0:
            rprint("[green]✓[/green] Existing session killed")

    script_dir = Path(__file__).parent
    layout_script = script_dir / "supervisor_layout.sh"

    os.execvp("bash", ["bash", str(layout_script), session])


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "0.0.0.0",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port to listen on")
    ] = 8080,
    session: SessionOption = "agents",
):
    """Start web dashboard server for remote monitoring.

    Provides a mobile-optimized read-only dashboard that displays
    agent status and timeline data. Auto-refreshes every 5 seconds.

    Access from your phone at http://<your-ip>:8080

    Examples:
        overcode serve                    # Listen on all interfaces, port 8080
        overcode serve --port 3000        # Custom port
        overcode serve --host 127.0.0.1   # Local only
    """
    from .web_server import run_server

    run_server(host=host, port=port, tmux_session=session)


@app.command()
def web(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port to listen on")
    ] = 8080,
):
    """Launch analytics web dashboard for browsing historical data.

    A lightweight web app for exploring session history, timeline
    visualization, and efficiency metrics. Uses Chart.js for
    interactive charts with dark theme matching the TUI.

    Features:
        - Dashboard with summary stats and daily activity charts
        - Session browser with sortable table
        - Timeline view with agent status and user presence
        - Efficiency metrics with cost analysis

    Time range presets can be configured in ~/.overcode/config.yaml:

        web:
          time_presets:
            - name: "Morning"
              start: "09:00"
              end: "12:00"

    Examples:
        overcode web                    # Start on localhost:8080
        overcode web --port 3000        # Custom port
        overcode web --host 0.0.0.0     # Listen on all interfaces
    """
    from .web_server import run_analytics_server

    run_analytics_server(host=host, port=port)




@app.command()
def export(
    output: Annotated[
        str, typer.Argument(help="Output file path (.parquet)")
    ],
    include_archived: Annotated[
        bool, typer.Option("--archived", "-a", help="Include archived sessions")
    ] = True,
    include_timeline: Annotated[
        bool, typer.Option("--timeline", "-t", help="Include timeline data")
    ] = True,
    include_presence: Annotated[
        bool, typer.Option("--presence", "-p", help="Include presence data")
    ] = True,
):
    """Export session data to Parquet format for Jupyter analysis.

    Creates a parquet file with session stats, timeline history,
    and presence data suitable for pandas/jupyter analysis.
    """
    from .data_export import export_to_parquet

    try:
        result = export_to_parquet(
            output,
            include_archived=include_archived,
            include_timeline=include_timeline,
            include_presence=include_presence,
        )
        rprint(f"[green]✓[/green] Exported to [bold]{output}[/bold]")
        rprint(f"  Sessions: {result['sessions_count']}")
        if include_archived:
            rprint(f"  Archived: {result['archived_count']}")
        if include_timeline:
            rprint(f"  Timeline rows: {result['timeline_rows']}")
        if include_presence:
            rprint(f"  Presence rows: {result['presence_rows']}")
    except ImportError as e:
        rprint(f"[red]Error:[/red] {e}")
        rprint("[dim]Install pyarrow: pip install pyarrow[/dim]")
        raise typer.Exit(1)
    except Exception as e:
        rprint(f"[red]Export failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def history(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name (omit for all archived)")
    ] = None,
):
    """Show archived session history."""
    from .session_manager import SessionManager
    from .tui_helpers import format_duration, format_tokens

    sessions = SessionManager()

    if name:
        # Show specific archived session
        archived = sessions.list_archived_sessions()
        session = next((s for s in archived if s.name == name), None)
        if not session:
            rprint(f"[red]✗[/red] No archived session named '[bold]{name}[/bold]'")
            raise typer.Exit(1)

        rprint(f"\n[bold]{session.name}[/bold]")
        rprint(f"  ID: {session.id}")
        rprint(f"  Started: {session.start_time}")
        end_time = getattr(session, '_end_time', None)
        if end_time:
            rprint(f"  Ended: {end_time}")
        rprint(f"  Directory: {session.start_directory or '-'}")
        rprint(f"  Repo: {session.repo_name or '-'} ({session.branch or '-'})")
        rprint(f"\n  [bold]Stats:[/bold]")
        stats = session.stats
        rprint(f"    Interactions: {stats.interaction_count}")
        rprint(f"    Tokens: {format_tokens(stats.total_tokens)}")
        rprint(f"    Cost: ${stats.estimated_cost_usd:.4f}")
        rprint(f"    Green time: {format_duration(stats.green_time_seconds)}")
        rprint(f"    Non-green time: {format_duration(stats.non_green_time_seconds)}")
        rprint(f"    Steers: {stats.steers_count}")
    else:
        # List all archived sessions
        archived = sessions.list_archived_sessions()
        if not archived:
            rprint("[dim]No archived sessions[/dim]")
            return

        rprint(f"\n[bold]Archived Sessions ({len(archived)}):[/bold]\n")
        for s in sorted(archived, key=lambda x: x.start_time, reverse=True):
            end_time = getattr(s, '_end_time', None)
            stats = s.stats
            duration = ""
            if end_time and s.start_time:
                try:
                    from datetime import datetime
                    start = datetime.fromisoformat(s.start_time)
                    end = datetime.fromisoformat(end_time)
                    dur_sec = (end - start).total_seconds()
                    duration = f" ({format_duration(dur_sec)})"
                except ValueError:
                    pass

            rprint(
                f"  {s.name:<16} {stats.interaction_count:>3}i "
                f"{format_tokens(stats.total_tokens):>6} "
                f"${stats.estimated_cost_usd:.2f}{duration}"
            )


# =============================================================================
# Monitor Daemon Commands
# =============================================================================


@monitor_daemon_app.callback(invoke_without_command=True)
def monitor_daemon_default(ctx: typer.Context, session: SessionOption = "agents"):
    """Show monitor daemon status (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _monitor_daemon_status(session)


@monitor_daemon_app.command("start")
def monitor_daemon_start(
    interval: Annotated[
        int, typer.Option("--interval", "-i", help="Polling interval in seconds")
    ] = 10,
    session: SessionOption = "agents",
):
    """Start the Monitor Daemon.

    The Monitor Daemon tracks session state and metrics:
    - Status detection (running, waiting, etc.)
    - Time accumulation (green_time, non_green_time)
    - Claude Code stats (tokens, interactions)
    - User presence state (macOS only)
    """
    from .monitor_daemon import MonitorDaemon, is_monitor_daemon_running, get_monitor_daemon_pid

    if is_monitor_daemon_running(session):
        pid = get_monitor_daemon_pid(session)
        rprint(f"[yellow]Monitor Daemon already running[/yellow] (PID {pid}) for session '{session}'")
        raise typer.Exit(1)

    rprint(f"[dim]Starting Monitor Daemon for session '{session}' with interval {interval}s...[/dim]")
    daemon = MonitorDaemon(session)
    daemon.run(interval)


@monitor_daemon_app.command("stop")
def monitor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Monitor Daemon."""
    from .monitor_daemon import stop_monitor_daemon, is_monitor_daemon_running, get_monitor_daemon_pid

    if not is_monitor_daemon_running(session):
        rprint(f"[dim]Monitor Daemon is not running for session '{session}'[/dim]")
        return

    pid = get_monitor_daemon_pid(session)
    if stop_monitor_daemon(session):
        rprint(f"[green]✓[/green] Monitor Daemon stopped (was PID {pid}) for session '{session}'")
    else:
        rprint("[red]Failed to stop Monitor Daemon[/red]")
        raise typer.Exit(1)


@monitor_daemon_app.command("status")
def monitor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Monitor Daemon status."""
    _monitor_daemon_status(session)


def _monitor_daemon_status(session: str):
    """Internal function for showing monitor daemon status."""
    from .monitor_daemon import is_monitor_daemon_running, get_monitor_daemon_pid
    from .monitor_daemon_state import get_monitor_daemon_state
    from .settings import get_monitor_daemon_state_path

    state_path = get_monitor_daemon_state_path(session)

    if not is_monitor_daemon_running(session):
        rprint(f"[dim]Monitor Daemon ({session}):[/dim] ○ stopped")
        state = get_monitor_daemon_state(session)
        if state and state.last_loop_time:
            from .tui_helpers import format_ago
            rprint(f"  [dim]Last active: {format_ago(state.last_loop_time)}[/dim]")
        return

    pid = get_monitor_daemon_pid(session)
    state = get_monitor_daemon_state(session)

    rprint(f"[green]Monitor Daemon ({session}):[/green] ● running (PID {pid})")
    if state:
        rprint(f"  Status: {state.status}")
        rprint(f"  Loop count: {state.loop_count}")
        rprint(f"  Interval: {state.current_interval}s")
        rprint(f"  Sessions: {len(state.sessions)}")
        if state.last_loop_time:
            from .tui_helpers import format_ago
            rprint(f"  Last loop: {format_ago(state.last_loop_time)}")
        if state.presence_available:
            rprint(f"  Presence: state={state.presence_state}, idle={state.presence_idle_seconds:.0f}s")


@monitor_daemon_app.command("watch")
def monitor_daemon_watch(session: SessionOption = "agents"):
    """Watch Monitor Daemon logs in real-time."""
    import subprocess
    from .settings import get_session_dir

    log_file = get_session_dir(session) / "monitor_daemon.log"

    if not log_file.exists():
        rprint(f"[red]Log file not found:[/red] {log_file}")
        rprint("[dim]The Monitor Daemon may not have run yet.[/dim]")
        raise typer.Exit(1)

    rprint(f"[dim]Watching {log_file} (Ctrl-C to stop)[/dim]")
    print("-" * 60)

    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        print("\nStopped watching.")


# =============================================================================
# Supervisor Daemon Commands
# =============================================================================


@supervisor_daemon_app.callback(invoke_without_command=True)
def supervisor_daemon_default(ctx: typer.Context, session: SessionOption = "agents"):
    """Show supervisor daemon status (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _supervisor_daemon_status(session)


@supervisor_daemon_app.command("start")
def supervisor_daemon_start(
    interval: Annotated[
        int, typer.Option("--interval", "-i", help="Polling interval in seconds")
    ] = 10,
    session: SessionOption = "agents",
):
    """Start the Supervisor Daemon.

    The Supervisor Daemon handles Claude orchestration:
    - Launches daemon claude when sessions need attention
    - Waits for daemon claude to complete
    - Tracks interventions and steers

    Requires Monitor Daemon to be running (reads session state from it).
    """
    from .supervisor_daemon import SupervisorDaemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    if is_supervisor_daemon_running(session):
        pid = get_supervisor_daemon_pid(session)
        rprint(f"[yellow]Supervisor Daemon already running[/yellow] (PID {pid}) for session '{session}'")
        raise typer.Exit(1)

    rprint(f"[dim]Starting Supervisor Daemon for session '{session}' with interval {interval}s...[/dim]")
    daemon = SupervisorDaemon(session)
    daemon.run(interval)


@supervisor_daemon_app.command("stop")
def supervisor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Supervisor Daemon."""
    from .supervisor_daemon import stop_supervisor_daemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    if not is_supervisor_daemon_running(session):
        rprint(f"[dim]Supervisor Daemon is not running for session '{session}'[/dim]")
        return

    pid = get_supervisor_daemon_pid(session)
    if stop_supervisor_daemon(session):
        rprint(f"[green]✓[/green] Supervisor Daemon stopped (was PID {pid}) for session '{session}'")
    else:
        rprint("[red]Failed to stop Supervisor Daemon[/red]")
        raise typer.Exit(1)


@supervisor_daemon_app.command("status")
def supervisor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Supervisor Daemon status."""
    _supervisor_daemon_status(session)


def _supervisor_daemon_status(session: str):
    """Internal function for showing supervisor daemon status."""
    from .supervisor_daemon import is_supervisor_daemon_running, get_supervisor_daemon_pid

    if not is_supervisor_daemon_running(session):
        rprint(f"[dim]Supervisor Daemon ({session}):[/dim] ○ stopped")
        return

    pid = get_supervisor_daemon_pid(session)
    rprint(f"[green]Supervisor Daemon ({session}):[/green] ● running (PID {pid})")


@supervisor_daemon_app.command("watch")
def supervisor_daemon_watch(session: SessionOption = "agents"):
    """Watch Supervisor Daemon logs in real-time."""
    import subprocess
    from .settings import get_session_dir

    log_file = get_session_dir(session) / "supervisor_daemon.log"

    if not log_file.exists():
        rprint(f"[red]Log file not found:[/red] {log_file}")
        rprint("[dim]The Supervisor Daemon may not have run yet.[/dim]")
        raise typer.Exit(1)

    rprint(f"[dim]Watching {log_file} (Ctrl-C to stop)[/dim]")
    print("-" * 60)

    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        print("\nStopped watching.")


# =============================================================================
# Config Commands
# =============================================================================

CONFIG_TEMPLATE = """\
# Overcode configuration
# Location: ~/.overcode/config.yaml

# Default instructions sent to new agents
# default_standing_instructions: "Be concise. Ask before making large changes."

# AI summarizer settings (for corporate API gateways)
# summarizer:
#   api_url: https://api.openai.com/v1/chat/completions
#   model: gpt-4o-mini
#   api_key_var: OPENAI_API_KEY  # env var containing the API key

# Cloud relay for remote monitoring
# relay:
#   enabled: false
#   url: https://your-worker.workers.dev/update
#   api_key: your-secret-key
#   interval: 30  # seconds between pushes

# Web dashboard time presets
# web:
#   time_presets:
#     - name: "Morning"
#       start: "09:00"
#       end: "12:00"
#     - name: "Full Day"
#       start: "09:00"
#       end: "17:00"

# Time context hook settings (for 'overcode time-context')
# time_context:
#   office_start: 9
#   office_end: 17
#   heartbeat_interval_minutes: 15  # omit to disable
"""


@config_app.callback(invoke_without_command=True)
def config_default(ctx: typer.Context):
    """Show current configuration (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _config_show()


@config_app.command("init")
def config_init(
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing config file")
    ] = False,
):
    """Create a config file with documented defaults.

    Creates ~/.overcode/config.yaml with all options commented out.
    Use --force to overwrite an existing config file.
    """
    from .config import CONFIG_PATH

    # Ensure directory exists
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if CONFIG_PATH.exists() and not force:
        rprint(f"[yellow]Config file already exists:[/yellow] {CONFIG_PATH}")
        rprint("[dim]Use --force to overwrite[/dim]")
        raise typer.Exit(1)

    CONFIG_PATH.write_text(CONFIG_TEMPLATE)
    rprint(f"[green]✓[/green] Created config file: [bold]{CONFIG_PATH}[/bold]")
    rprint("[dim]Edit to customize your settings[/dim]")


@config_app.command("show")
def config_show():
    """Show current configuration."""
    _config_show()


def _config_show():
    """Internal function to display current config."""
    from .config import CONFIG_PATH, load_config

    if not CONFIG_PATH.exists():
        rprint(f"[dim]No config file found at {CONFIG_PATH}[/dim]")
        rprint("[dim]Run 'overcode config init' to create one[/dim]")
        return

    config = load_config()
    if not config:
        rprint(f"[dim]Config file is empty: {CONFIG_PATH}[/dim]")
        return

    rprint(f"[bold]Configuration[/bold] ({CONFIG_PATH}):\n")

    # Show each configured section
    if "default_standing_instructions" in config:
        instr = config["default_standing_instructions"]
        display = instr[:60] + "..." if len(instr) > 60 else instr
        rprint(f"  default_standing_instructions: \"{display}\"")

    if "summarizer" in config:
        s = config["summarizer"]
        rprint("  summarizer:")
        if "api_url" in s:
            rprint(f"    api_url: {s['api_url']}")
        if "model" in s:
            rprint(f"    model: {s['model']}")
        if "api_key_var" in s:
            rprint(f"    api_key_var: {s['api_key_var']}")

    if "relay" in config:
        r = config["relay"]
        rprint("  relay:")
        rprint(f"    enabled: {r.get('enabled', False)}")
        if "url" in r:
            rprint(f"    url: {r['url']}")
        if "interval" in r:
            rprint(f"    interval: {r['interval']}s")

    if "web" in config:
        w = config["web"]
        if "time_presets" in w:
            rprint(f"  web.time_presets: {len(w['time_presets'])} presets")


@config_app.command("path")
def config_path():
    """Show the config file path."""
    from .config import CONFIG_PATH
    print(CONFIG_PATH)


# =============================================================================
# Entry Point
# =============================================================================


def main():
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
