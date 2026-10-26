"""
CLI: background shells of Claude agents (#515).

Lists what each agent's background shells are (service, batch work or a
watcher) and stops one when asked. overcode never stops a shell by itself:
a stuck watcher only gets a badge, and this command is how the person or
the overagent tidies it up.
"""

from typing import Annotated, List, Optional, Tuple

import typer
from rich import print as rprint

from ._shared import app, SessionOption


def _published(session: str) -> List[Tuple[str, list]]:
    """(agent name, its shells) for every agent the daemon published shells for."""
    from ..monitor_daemon_state import get_monitor_daemon_state

    state = get_monitor_daemon_state(session)
    if state is None:
        return []
    return [(s.name, s.background_shells) for s in state.sessions
            if isinstance(s.background_shells, list) and s.background_shells]


def _line(shell: dict, now: float) -> str:
    from ..status_constants import BADGE_KINDS
    from ..tui_helpers import format_duration

    stuck = shell.get("stuck_since") is not None
    badge = "bg_stuck" if stuck else f"bg_{shell.get('kind')}"
    glyph = BADGE_KINDS.get(badge, ("·",))[0]
    since = shell.get("since")
    age = format_duration(int(now - since)) if isinstance(since, (int, float)) else "?"
    command = " ".join(str(shell.get("command") or "").split())
    out = f"  {glyph} {shell.get('kind', '?'):<8} {age:>6}  {command}"
    if shell.get("target"):
        state = shell.get("target_state")
        if stuck:
            out += f"\n      [bold yellow]stuck:[/bold yellow] {shell['target']} has " \
                   f"{'ended' if state == 'ended' else 'gone'}"
        else:
            out += f"\n      [dim]watching {shell['target']}" + (f" ({state})" if state else "") + "[/dim]"
    out += f"\n      [dim]pid {shell['pid']} · id {shell.get('id')}[/dim]" if shell.get("pid") \
        else f"\n      [dim]id {shell.get('id')} (process not found)[/dim]"
    return out


def _stop_tree(pid: int, command: str) -> Optional[str]:
    """Stop the shell ``pid`` and everything under it; an error message, or None.

    Refuses unless ``pid`` is still a Claude Bash-tool shell running
    ``command`` (a pid can be reused).
    """
    import os
    import signal
    from ..background_shells import COMMAND_CHARS, shell_command
    from ..process_resources import build_children_index, descendant_pids, snapshot_processes

    table = snapshot_processes()
    info = table.get(pid)
    if info is None:
        return f"pid {pid} is not running"
    live = shell_command(info.argv)
    if live is None or live[:COMMAND_CHARS] != command[:COMMAND_CHARS]:
        return f"pid {pid} is no longer that shell"
    pids = descendant_pids(pid, build_children_index(table), max_depth=12)
    # Children first, so a loop can't start another round in between
    for p in reversed(pids):
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            return f"not allowed to stop pid {p}"
    return None


@app.command()
def shells(
    name: Annotated[Optional[str], typer.Argument(help="Agent (default: every agent with background shells)")] = None,
    kill: Annotated[Optional[str], typer.Option("--kill", "-k", help="Stop a background shell: its pid or shell id")] = None,
    stuck: Annotated[bool, typer.Option("--stuck", help="Only stuck watchers")] = False,
    session: SessionOption = "agents",
):
    """Background shells of Claude agents: services, batch work and watchers.

    A watcher whose target (an overcode agent or job) has ended but which is
    still polling is stuck: it will never fire, and it holds its agent yellow.
    overcode only flags it; stop it here with --kill.

    Examples:
        overcode shells
        overcode shells my-agent
        overcode shells --stuck
        overcode shells --kill 48213
    """
    import time

    published = _published(session)
    if name is not None:
        from ..session_manager import SessionManager
        from ._shared import find_agent

        agent = find_agent(SessionManager(), name)
        wanted = agent.name if agent is not None else name
        published = [(n, s) for n, s in published if n == wanted]

    if kill is not None:
        matches = [(n, s) for n, shells_ in published for s in shells_
                   if str(s.get("pid")) == kill or s.get("id") == kill]
        if not matches:
            rprint(f"[red]✗[/red] No background shell '{kill}'"
                   + (f" for agent '{name}'" if name else "") + " (see overcode shells)")
            raise typer.Exit(1)
        agent_name, shell = matches[0]
        if not shell.get("pid"):
            rprint(f"[red]✗[/red] Shell {shell.get('id')} has no known process to stop")
            raise typer.Exit(1)
        error = _stop_tree(int(shell["pid"]), str(shell.get("command") or ""))
        if error:
            rprint(f"[red]✗[/red] Not stopped: {error}")
            raise typer.Exit(1)
        rprint(f"[green]✓[/green] Stopped {agent_name}'s background shell {shell['pid']}: "
               f"{' '.join(str(shell.get('command') or '').split())[:80]}")
        rprint("  [dim]Claude tells the agent the shell ended on its next turn[/dim]")
        return

    now = time.time()
    shown = 0
    for agent_name, shells_ in published:
        if stuck:
            shells_ = [s for s in shells_ if s.get("stuck_since") is not None]
        if not shells_:
            continue
        rprint(f"[bold]{agent_name}[/bold]")
        for shell in shells_:
            rprint(_line(shell, now))
        shown += len(shells_)
    if not shown:
        rprint("[dim]No stuck watchers[/dim]" if stuck else "[dim]No background shells[/dim]")
