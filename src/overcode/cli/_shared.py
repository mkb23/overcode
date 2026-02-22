"""
Shared CLI state: Typer apps, console, options, and utilities.
"""

import sys
from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich import print as rprint
from rich.console import Console

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

# Skills subcommand group
skills_app = typer.Typer(
    name="skills",
    help="Manage Claude Code skill files.",
    no_args_is_help=True,
)
app.add_typer(skills_app, name="skills")

# Perms subcommand group
perms_app = typer.Typer(
    name="perms",
    help="Manage Claude Code tool permissions for overcode commands.",
    no_args_is_help=True,
)
app.add_typer(perms_app, name="perms")

# Budget subcommand group (#244)
budget_app = typer.Typer(
    name="budget",
    help="Manage agent cost budgets.",
    no_args_is_help=True,
)
app.add_typer(budget_app, name="budget")

# Sister subcommand group
sister_app = typer.Typer(
    name="sister",
    help="Manage sister instances for cross-machine monitoring.",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(sister_app, name="sister")

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


def _parse_duration(s: str) -> float:
    """Parse a duration string like '5m', '1h', '30s', '90' into seconds."""
    s = s.strip().lower()
    if s.endswith('s'):
        return float(s[:-1])
    elif s.endswith('m'):
        return float(s[:-1]) * 60
    elif s.endswith('h'):
        return float(s[:-1]) * 3600
    else:
        return float(s)


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """Launch the TUI monitor when no command is given."""
    if ctx.invoked_subcommand is None:
        from ..tui import run_tui

        run_tui("agents")
