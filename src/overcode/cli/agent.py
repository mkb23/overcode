"""
Agent commands: launch, list, attach, kill, follow, report, cleanup,
set-value, set-budget, annotate, send, show.
"""

from typing import Annotated, Optional, List

import typer
from rich import print as rprint

from ..launcher import ClaudeLauncher
from ._shared import app, SessionOption, _parse_duration


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
    on_stuck: Annotated[
        Optional[str],
        typer.Option("--on-stuck", help="Policy when child stops: wait (default), fail, timeout:DURATION"),
    ] = None,
    oversight_timeout: Annotated[
        Optional[str],
        typer.Option("--oversight-timeout", help="Shorthand for --on-stuck timeout:DURATION (e.g. 5m, 1h)"),
    ] = None,
    allowed_tools: Annotated[
        Optional[str],
        typer.Option("--allowed-tools", help="Comma-separated tools for Claude (e.g. 'Bash,Read,Write,Edit')"),
    ] = None,
    claude_args: Annotated[
        Optional[List[str]],
        typer.Option("--claude-arg", help="Extra Claude CLI flag (repeatable, e.g. '--model haiku')"),
    ] = None,
    session: SessionOption = "agents",
):
    """Launch a new Claude agent."""
    import os

    # Parse oversight policy
    oversight_policy = "wait"
    oversight_timeout_seconds = 0.0
    if oversight_timeout:
        oversight_policy = "timeout"
        try:
            oversight_timeout_seconds = _parse_duration(oversight_timeout)
        except ValueError:
            rprint(f"[red]Error: Invalid duration '{oversight_timeout}'[/red]")
            raise typer.Exit(code=1)
    elif on_stuck:
        if on_stuck == "wait":
            oversight_policy = "wait"
        elif on_stuck == "fail":
            oversight_policy = "fail"
        elif on_stuck.startswith("timeout:"):
            oversight_policy = "timeout"
            try:
                oversight_timeout_seconds = _parse_duration(on_stuck[len("timeout:"):])
            except ValueError:
                rprint(f"[red]Error: Invalid duration in '--on-stuck {on_stuck}'[/red]")
                raise typer.Exit(code=1)
        else:
            rprint(f"[red]Error: Invalid --on-stuck value '{on_stuck}'. Use: wait, fail, timeout:DURATION[/red]")
            raise typer.Exit(code=1)

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
        allowed_tools=allowed_tools,
        extra_claude_args=claude_args,
    )

    if result:
        rprint(f"\n[green]✓[/green] Agent '[bold]{name}[/bold]' launched")
        if result.parent_session_id:
            rprint(f"  Parent: {parent or os.environ.get('OVERCODE_SESSION_NAME', '?')}")
        if prompt:
            rprint("  Initial prompt sent")
        if allowed_tools:
            rprint(f"  Allowed tools: {allowed_tools}")
        if claude_args:
            rprint(f"  Extra Claude args: {' '.join(claude_args)}")

        # Store oversight policy on session
        if oversight_policy != "wait" or oversight_timeout_seconds > 0:
            from ..session_manager import SessionManager
            sm = SessionManager()
            sm.update_session(
                result.id,
                oversight_policy=oversight_policy,
                oversight_timeout_seconds=oversight_timeout_seconds,
            )
            rprint(f"  Oversight: {oversight_policy}" + (f" ({oversight_timeout_seconds:.0f}s)" if oversight_timeout_seconds > 0 else ""))

        # Skill staleness check (#290)
        from ..bundled_skills import any_skills_stale
        if any_skills_stale():
            rprint("[yellow]Warning:[/yellow] Installed skills are modified. Run [bold]overcode skills install[/bold] to update.")

        if follow:
            from ..follow_mode import follow_agent
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
    cost: Annotated[
        bool, typer.Option("--cost", help="Show $ cost instead of token counts")
    ] = False,
    sisters: Annotated[
        bool, typer.Option("--sisters", help="Include sister (remote) agents")
    ] = False,
    session: SessionOption = "agents",
):
    """List running agents with status.

    Shows a compact TUI-style summary line per agent with status, time-in-state,
    timing breakdown, token/cost usage, context window %, and git diff stats.

    With no arguments, shows all agents with depth-based indentation.
    With a name, shows that agent + all its descendants.
    """
    from ..history_reader import get_session_stats
    from ..tui_helpers import (
        get_current_state_times, get_status_symbol, get_git_diff_stats,
    )
    from ..monitor_daemon_state import get_monitor_daemon_state
    from ..summary_columns import build_cli_context, SUMMARY_COLUMNS
    from ..tui_logic import compute_tree_metadata, sort_sessions_by_tree
    from rich.text import Text
    from rich.console import Console

    launcher = ClaudeLauncher(session)
    sessions = launcher.list_sessions()

    # Merge sister sessions if --sisters flag
    if sisters:
        from ..sister_poller import SisterPoller
        poller = SisterPoller()
        if poller.has_sisters:
            remote_sessions = poller.poll_all()
            sessions = sessions + remote_sessions

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

    if not sessions:
        rprint("[dim]No running agents[/dim]")
        return

    # Sort in tree order and compute tree metadata for depth/prefix
    sessions = sort_sessions_by_tree(sessions)
    tree_meta = compute_tree_metadata(sessions)

    # Columns to render in list mode (subset of TUI columns)
    list_columns = {
        "status_symbol", "time_in_state", "sleep_countdown", "agent_name",
        "git_diff",
        "uptime", "running_time", "stalled_time", "sleep_time",
        "token_count", "cost", "budget", "context_usage",
    }

    # Pre-compute: any agent with budget, max name width
    any_has_budget = any(s.cost_budget_usd > 0 for s in sessions)
    max_name_len = max(len(s.name) for s in sessions)
    name_width = min(max(max_name_len, 10), 20)

    # Prefer daemon state for status/activity (single source of truth)
    daemon_state = get_monitor_daemon_state(session)
    use_daemon = daemon_state is not None and not daemon_state.is_stale()

    # Only create detector as fallback when daemon isn't running
    detector = None
    if not use_daemon:
        from ..status_detector_factory import StatusDetectorDispatcher
        detector = StatusDetectorDispatcher(session)

    console = Console()
    terminated_count = 0

    # First pass: collect status/activity/stats per session
    session_data = []
    for sess in sessions:
        if getattr(sess, 'is_remote', False):
            status = sess.stats.current_state or "running"
            activity = sess.stats.current_task or ""
        elif sess.status == "terminated":
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
                if detector is None:
                    from ..status_detector_factory import StatusDetectorDispatcher
                    detector = StatusDetectorDispatcher(session)
                status, activity, _ = detector.detect_status(sess)
        else:
            status, activity, _ = detector.detect_status(sess)

        if sess.is_asleep:
            status = "asleep"

        claude_stats = None
        try:
            claude_stats = get_session_stats(sess)
        except Exception:
            pass

        git_diff = None
        if getattr(sess, 'is_remote', False):
            git_diff = getattr(sess, 'remote_git_diff', None)
        else:
            try:
                if sess.start_directory:
                    git_diff = get_git_diff_stats(sess.start_directory)
            except Exception:
                pass

        session_data.append((sess, status, activity, claude_stats, git_diff))

    # Compute cross-session flags
    any_is_sleeping = any(st == "busy_sleeping" for _, st, _, _, _ in session_data)

    # Second pass: render
    for sess, status, activity, claude_stats, git_diff in session_data:
        meta = tree_meta.get(sess.id)
        child_count = meta.child_count if meta else 0
        ctx = build_cli_context(
            session=sess, stats=sess.stats,
            claude_stats=claude_stats, git_diff_stats=git_diff,
            status=status, bg_bash_count=0, live_sub_count=0,
            any_has_budget=any_has_budget, child_count=child_count,
            any_is_sleeping=any_is_sleeping,
        )

        # Enable colors and set detail level for list view
        ctx.monochrome = False
        _, status_color = get_status_symbol(status)
        ctx.status_color = f"bold {status_color}"
        ctx.summary_detail = "med"
        ctx.show_cost = cost

        # Handle tree indentation (#244) using compute_tree_metadata
        depth = meta.depth if meta else 0
        indent = "  " * depth
        available = name_width - len(indent)
        ctx.display_name = (indent + sess.name[:available]).ljust(name_width)

        # Render line using column system
        line = Text()
        for col in SUMMARY_COLUMNS:
            if col.id not in list_columns:
                continue
            if ctx.summary_detail not in col.detail_levels:
                continue
            segments = col.render(ctx)
            if segments:
                for text, style in segments:
                    line.append(text, style=style)

        # Append activity (truncate to fit terminal width)
        line.append(" │ ", style="dim")
        line.append(activity)
        line.truncate(console.width, pad=False)

        console.print(line, no_wrap=True)

    if terminated_count > 0:
        rprint(f"\n[dim]{terminated_count} terminated session(s). Run 'overcode cleanup' to remove.[/dim]")


@app.command()
def attach(
    name: Annotated[Optional[str], typer.Argument(help="Agent name to focus on")] = None,
    bare: Annotated[
        bool,
        typer.Option("--bare", help="Minimal attach: no status bar, no prefix, mouse passthrough"),
    ] = False,
    session: SessionOption = "agents",
):
    """Attach to the tmux session to view agents.

    Optionally specify an agent name to jump directly to that agent's window.
    Use --bare for embedding in VSCode or other terminals (hides tmux chrome).
    """
    launcher = ClaudeLauncher(session)
    if bare:
        if not name:
            rprint("[red]Error:[/red] --bare requires an agent name")
            raise typer.Exit(1)
        rprint(f"[dim]Attaching to '{name}' (bare mode, close terminal to detach)...[/dim]")
    elif name:
        rprint(f"[dim]Attaching to '{name}'...[/dim]")
        rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    else:
        rprint("[dim]Attaching to overcode...[/dim]")
        rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    launcher.attach(name=name, bare=bare)


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
    from ..follow_mode import follow_agent as _follow_agent

    exit_code = _follow_agent(name, session)
    raise typer.Exit(code=exit_code)


@app.command()
def report(
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="Report status: success or failure"),
    ],
    reason: Annotated[
        Optional[str],
        typer.Option("--reason", "-r", help="Reason for the report"),
    ] = None,
):
    """Report completion status from a child agent.

    Called by child agents to signal success or failure to the parent.
    Reads OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION from env vars
    (automatically set for all child agents).

    Examples:
        overcode report --status success
        overcode report --status failure --reason "Tests failed"
    """
    import os
    import json
    from datetime import datetime
    from pathlib import Path

    if status not in ("success", "failure"):
        rprint(f"[red]Error: --status must be 'success' or 'failure', got '{status}'[/red]")
        raise typer.Exit(code=1)

    agent_name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux_session = os.environ.get("OVERCODE_TMUX_SESSION")

    if not agent_name or not tmux_session:
        rprint("[red]Error: OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION env vars required[/red]")
        rprint("[dim]This command should be run from within a child agent launched by overcode[/dim]")
        raise typer.Exit(code=1)

    # Write report file
    from ..settings import get_session_dir
    session_dir = get_session_dir(tmux_session)
    report_file = session_dir / f"report_{agent_name}.json"
    report_data = {
        "status": status,
        "reason": reason or "",
        "timestamp": datetime.now().isoformat(),
    }
    report_file.write_text(json.dumps(report_data, indent=2))

    # Also update session fields for persistence
    from ..session_manager import SessionManager
    sm = SessionManager()
    session = sm.get_session_by_name(agent_name)
    if session:
        sm.update_session(
            session.id,
            report_status=status,
            report_reason=reason or "",
        )

    rprint(f"[green]✓[/green] Report filed: {status}" + (f" ({reason})" if reason else ""))


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
    from ..session_manager import SessionManager

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
    from ..session_manager import SessionManager

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
    from ..session_manager import SessionManager

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
    from ..history_reader import get_session_stats
    from ..status_patterns import extract_background_bash_count, extract_live_subagent_count, strip_ansi
    from ..tui_helpers import get_git_diff_stats
    from ..summary_columns import build_cli_context, render_cli_stats
    from ..monitor_daemon_state import get_monitor_daemon_state

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
        from ..status_detector_factory import create_status_detector
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
        from ..status_detector_factory import StatusDetectorDispatcher
        dispatcher = StatusDetectorDispatcher(session)
        pane_content_raw = dispatcher.get_pane_content(sess.tmux_window, num_lines=lines)

    if not no_stats:
        # Gather all stats
        bg_bash_count = extract_background_bash_count(pane_content_raw) if pane_content_raw else 0
        live_sub_count = extract_live_subagent_count(pane_content_raw) if pane_content_raw else 0

        claude_stats = None
        try:
            claude_stats = get_session_stats(sess)
            if claude_stats:
                live_sub_count = max(live_sub_count, claude_stats.live_subagent_count)
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

        # Claude CLI flag passthrough (#290)
        if sess.allowed_tools:
            print(f"{'Tools:':<{label_width + 1}} {sess.allowed_tools}")
        if sess.extra_claude_args:
            print(f"{'Claude args:':<{label_width + 1}} {' '.join(sess.extra_claude_args)}")

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
