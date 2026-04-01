"""
CLI commands for managing overcode jobs.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, jobs_app


@app.command()
def bash(
    command: Annotated[str, typer.Argument(help="Bash command to run as a tracked job")],
    name: Annotated[Optional[str], typer.Option("--name", "-n", help="Job name (auto-derived if omitted)")] = None,
    directory: Annotated[str, typer.Option("--directory", "-d", help="Working directory")] = ".",
    agent: Annotated[Optional[str], typer.Option("--agent", "-a", help="Link to an agent session by name")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Attach to the job's tmux window after launch")] = True,
):
    """Launch a bash command as a tracked job."""
    import os
    from ..job_launcher import JobLauncher
    from ..session_manager import SessionManager

    directory = os.path.abspath(directory)

    agent_session_id = None
    agent_name = None
    # Auto-detect calling agent from env if --agent not specified
    if not agent:
        agent = os.environ.get("OVERCODE_SESSION_NAME")
    if agent:
        sm = SessionManager()
        sess = sm.get_session_by_name(agent)
        if sess:
            agent_session_id = sess.id
            agent_name = sess.name
        else:
            rprint(f"[yellow]Warning: Agent '{agent}' not found, launching without link[/yellow]")

    launcher = JobLauncher()
    job = launcher.launch(
        command=command,
        name=name,
        directory=directory,
        agent_session_id=agent_session_id,
        agent_name=agent_name,
    )

    rprint(f"[green]✓[/green] Job '[bold]{job.name}[/bold]' launched")
    rprint(f"  Command: {job.command}")
    rprint(f"  Window: {job.tmux_session}:{job.tmux_window}")
    if agent_name:
        rprint(f"  Linked to agent: {agent_name}")

    if follow:
        if os.isatty(0):
            launcher.attach(job.name)
        else:
            rprint(f"  [dim]No TTY — use 'overcode jobs tail {job.name}' to stream output[/dim]")


@jobs_app.command("list")
def list_jobs(
    all: Annotated[bool, typer.Option("--all", "-a", help="Include completed/failed/killed jobs")] = False,
):
    """List tracked jobs."""
    from ..job_launcher import JobLauncher

    launcher = JobLauncher()
    jobs = launcher.list_jobs(include_completed=all)

    if not jobs:
        rprint("[dim]No jobs running[/dim]" if not all else "[dim]No jobs found[/dim]")
        return

    for job in sorted(jobs, key=lambda j: j.start_time, reverse=True):
        status_icon = {
            "running": "[green]●[/green]",
            "completed": "[green]✓[/green]",
            "failed": "[red]✗[/red]",
            "killed": "[yellow]✗[/yellow]",
        }.get(job.status, "?")

        exit_str = ""
        if job.exit_code is not None:
            exit_str = f" ({job.exit_code})"

        duration = ""
        if job.start_time:
            from datetime import datetime
            try:
                start = datetime.fromisoformat(job.start_time)
                end = datetime.fromisoformat(job.end_time) if job.end_time else datetime.now()
                dur_sec = (end - start).total_seconds()
                mins, secs = divmod(int(dur_sec), 60)
                duration = f"{mins}m{secs:02d}s"
            except ValueError:
                pass

        agent_str = f" ← {job.agent_name}" if job.agent_name else ""
        rprint(
            f"  {status_icon} {job.name:<16} {job.command:<30} {duration:>8}   "
            f"{job.status}{exit_str}{agent_str}"
        )


@jobs_app.command("kill")
def kill_job(
    name: Annotated[str, typer.Argument(help="Job name to kill")],
):
    """Kill a running job."""
    from ..job_launcher import JobLauncher

    launcher = JobLauncher()
    if launcher.kill_job(name):
        rprint(f"[green]✓[/green] Job '[bold]{name}[/bold]' killed")
    else:
        rprint(f"[red]✗[/red] Could not kill job '{name}' (not found or not running)")
        raise typer.Exit(1)


@jobs_app.command("attach")
def attach_job(
    name: Annotated[str, typer.Argument(help="Job name to attach to")],
    bare: Annotated[bool, typer.Option("--bare", "-b", help="Attach with stripped tmux chrome")] = False,
):
    """Attach to a job's tmux window."""
    from ..job_launcher import JobLauncher

    launcher = JobLauncher()
    try:
        launcher.attach(name, bare=bare)
    except ValueError as e:
        rprint(f"[red]✗[/red] {e}")
        raise typer.Exit(1)


@jobs_app.command("clear")
def clear_completed():
    """Remove all completed/failed/killed jobs."""
    from ..job_manager import JobManager

    manager = JobManager()
    manager.clear_completed()
    rprint("[green]✓[/green] Cleared completed jobs")


@jobs_app.command("tail")
def tail_job(
    name: Annotated[str, typer.Argument(help="Job name to tail")],
    lines: Annotated[Optional[int], typer.Option("--lines", "-n", help="Show last N lines and exit")] = None,
    follow: Annotated[bool, typer.Option("--follow/--no-follow", "-f", help="Stream output until job completes")] = True,
    poll_interval: Annotated[float, typer.Option("--poll", hidden=True)] = 0.5,
):
    """Stream a job's output (like tail -f). Works without a TTY.

    Default: streams output until the job completes or Ctrl-C.
    With --lines N: shows last N lines and exits.

    Examples:
        overcode jobs tail my-job
        overcode jobs tail my-job --lines 50
        overcode jobs tail my-job --no-follow
    """
    import signal
    import sys
    import time
    from collections import deque
    from ..follow_mode import _capture_pane, _find_dedup_start
    from ..job_manager import JobManager
    from ..status_patterns import strip_ansi

    manager = JobManager()
    job = manager.get_job_by_name(name)
    if not job:
        rprint(f"[red]Error: Job '{name}' not found[/red]")
        raise typer.Exit(1)

    tmux_session = job.tmux_session
    window_name = job.tmux_window

    # One-shot: capture and print last N lines
    if lines is not None:
        raw = _capture_pane(tmux_session, window_name, lines=lines)
        if raw:
            for line in raw.rstrip().split('\n'):
                cleaned = strip_ansi(line).strip()
                if cleaned:
                    print(cleaned)
        return

    # Streaming mode
    interrupted = False

    def _sigint(sig, frame):
        nonlocal interrupted
        interrupted = True

    old_handler = signal.signal(signal.SIGINT, _sigint)

    recent_lines: deque = deque(maxlen=50)

    try:
        while not interrupted:
            raw = _capture_pane(tmux_session, window_name)
            if raw:
                new_lines = []
                for line in raw.rstrip().split('\n'):
                    cleaned = strip_ansi(line).strip()
                    new_lines.append(cleaned)

                output_start = _find_dedup_start(new_lines, recent_lines)
                if output_start < len(new_lines):
                    for line in new_lines[output_start:]:
                        if line:
                            print(line)
                        recent_lines.append(line)

            # Check if job is done
            if follow:
                job = manager.get_job_by_name(name)
                if job and job.status in ("completed", "failed", "killed"):
                    # Final capture
                    time.sleep(poll_interval)
                    raw = _capture_pane(tmux_session, window_name)
                    if raw:
                        new_lines = [strip_ansi(l).strip() for l in raw.rstrip().split('\n')]
                        output_start = _find_dedup_start(new_lines, recent_lines)
                        for line in new_lines[output_start:]:
                            if line:
                                print(line)
                            recent_lines.append(line)
                    status_msg = f"completed (exit {job.exit_code})" if job.exit_code is not None else job.status
                    print(f"\n[tail] Job '{name}' {status_msg}", file=sys.stderr)
                    raise typer.Exit(0 if job.status == "completed" else 1)
            else:
                # --no-follow: one capture cycle then exit
                break

            time.sleep(poll_interval)
    finally:
        signal.signal(signal.SIGINT, old_handler)

    if interrupted:
        raise typer.Exit(130)


@jobs_app.command("_complete", hidden=True)
def mark_complete(
    job_id: Annotated[str, typer.Argument(help="Job ID")],
    exit_code: Annotated[int, typer.Argument(help="Exit code")],
):
    """Internal: mark a job as complete (called by wrapper script)."""
    from ..job_manager import JobManager

    manager = JobManager()
    manager.mark_complete(job_id, exit_code)
