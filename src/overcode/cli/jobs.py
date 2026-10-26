"""
CLI commands for managing overcode jobs.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, jobs_app, find_agent


@app.command()
def bash(
    command: Annotated[str, typer.Argument(help="Bash command to run as a tracked job")],
    name: Annotated[Optional[str], typer.Option("--name", "-n", help="Job name (auto-derived if omitted)")] = None,
    directory: Annotated[str, typer.Option("--directory", "-d", help="Working directory")] = ".",
    agent: Annotated[Optional[str], typer.Option("--agent", "-a", help="Link to an agent session by name")] = None,
    follow: Annotated[bool, typer.Option("--follow/--no-follow", "-f", help="Attach to the job's tmux window after launch")] = True,
):
    """Launch a bash command as a tracked job."""
    import os
    from ..agent_identity import current_agent, env_agent_id, env_agent_name, in_agent
    from ..job_launcher import JobLauncher
    from ..session_manager import SessionManager

    directory = os.path.abspath(directory)

    agent_session_id = None
    agent_name = None
    # Auto-detect calling agent from env if --agent not specified: by its
    # id, which a rename keeps (by launch name only for a pre-0.6 agent)
    sess = None
    if agent:
        sess = find_agent(SessionManager(), agent)
    elif in_agent():
        sess = current_agent(SessionManager())
        agent = env_agent_name() or env_agent_id()
    if agent:
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

    running = sum(1 for j in jobs if j.status == "running")
    rprint(f"[dim]{running} running[/dim]" + (f"[dim], {len(jobs) - running} finished[/dim]" if all else ""))


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


@jobs_app.command("wait")
def wait_job(
    name: Annotated[str, typer.Argument(help="Job name to wait for")],
    until: Annotated[Optional[str], typer.Option("--until", "-u", help="Regex: return as soon as a line of the job's output matches")] = None,
    timeout: Annotated[Optional[str], typer.Option("--timeout", "-t", help="Give up after this long (e.g. 90s, 10m, 1h)")] = None,
    poll_interval: Annotated[float, typer.Option("--poll", hidden=True)] = 1.0,
):
    """Wait for a job: until its output matches --until, or until it ends.

    It never outlives its job, so a watcher written with it can't get stuck
    polling a job that already ended or never started (#515). Use it rather
    than a hand-rolled `until … grep …; do sleep N; done` loop.

    Exit codes: 0 when --until matched (or, without --until, the job
    completed with exit 0); 1 when the job failed, was killed or ended
    without a match; 2 when there is no such job; 124 on --timeout.

    Examples:
        overcode jobs wait build
        overcode jobs wait server --until 'Listening on' --timeout 2m
    """
    import re
    import sys
    import time
    from ..follow_mode import _capture_pane
    from ..job_launcher import JobLauncher
    from ..status_patterns import strip_ansi
    from ._shared import _parse_duration

    try:
        pattern = re.compile(until) if until else None
    except re.error as e:
        rprint(f"[red]Error: --until is not a valid regex: {e}[/red]")
        raise typer.Exit(2)
    try:
        deadline = time.monotonic() + _parse_duration(timeout) if timeout else None
    except ValueError:
        rprint(f"[red]Error: --timeout '{timeout}' is not a duration (e.g. 90s, 10m, 1h)[/red]")
        raise typer.Exit(2)

    launcher = JobLauncher()

    def current():
        return next((j for j in launcher.list_jobs(include_completed=True) if j.name == name), None)

    def matched_line(job) -> Optional[str]:
        raw = _capture_pane(job.tmux_session, job.tmux_window, lines=500)
        for line in (raw or "").splitlines():
            cleaned = strip_ansi(line).strip()
            # The job header echoes the command, which may hold the pattern
            if cleaned.startswith("│ Command: "):
                continue
            if pattern.search(cleaned):
                return cleaned
        return None

    job = current()
    if job is None:
        rprint(f"[red]Error: Job '{name}' not found[/red]")
        raise typer.Exit(2)

    while True:
        ended = job.status != "running"
        if pattern is not None:
            # Checked once more after the end: the last lines may hold it
            line = matched_line(job)
            if line is not None:
                print(line)
                raise typer.Exit(0)
        if ended:
            exit_str = f" (exit {job.exit_code})" if job.exit_code is not None else ""
            print(f"[wait] Job '{name}' {job.status}{exit_str}"
                  + (" without a match" if pattern is not None else ""), file=sys.stderr)
            raise typer.Exit(0 if pattern is None and job.status == "completed" else 1)
        if deadline is not None and time.monotonic() >= deadline:
            print(f"[wait] Timed out after {timeout}; job '{name}' still running", file=sys.stderr)
            raise typer.Exit(124)
        time.sleep(poll_interval)
        job = current()
        if job is None:
            print(f"[wait] Job '{name}' is gone", file=sys.stderr)
            raise typer.Exit(1)


@jobs_app.command("_complete", hidden=True)
def mark_complete(
    job_id: Annotated[str, typer.Argument(help="Job ID")],
    exit_code: Annotated[int, typer.Argument(help="Exit code")],
):
    """Internal: mark a job as complete (called by wrapper script)."""
    from ..job_manager import JobManager

    manager = JobManager()
    manager.mark_complete(job_id, exit_code)
