"""
Config commands: init, show, path.
"""

from typing import Annotated

import typer
from rich import print as rprint

from ._shared import config_app


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

# Sister instances for cross-machine monitoring
# sisters:
#   - name: "macbook-pro"
#     url: "http://localhost:15337"
#   - name: "desktop"
#     url: "http://192.168.1.10:5337"
#     api_key: "shared-secret"
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
    from ..config import CONFIG_PATH

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
    from ..config import CONFIG_PATH, load_config

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

    if "sisters" in config:
        sisters = config["sisters"]
        if isinstance(sisters, list) and sisters:
            rprint(f"  sisters: {len(sisters)} configured")
            for s in sisters:
                if isinstance(s, dict) and s.get("name"):
                    rprint(f"    - {s['name']}: {s.get('url', '?')}")


@config_app.command("path")
def config_path():
    """Show the config file path."""
    from ..config import CONFIG_PATH
    print(CONFIG_PATH)
