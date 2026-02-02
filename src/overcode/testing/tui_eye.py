"""tui-eye: Visual TUI testing tool for Claude Code.

This CLI tool allows Claude Code to "see" TUI applications by:
1. Running them in a controlled tmux session
2. Capturing screenshots as PNG images
3. Sending keystrokes for interaction

Example usage:
    tui-eye start "overcode supervisor" --size 120x40
    tui-eye screenshot /tmp/tui.png
    tui-eye send j j enter
    tui-eye wait-for "Session:"
    tui-eye stop
"""

import sys
from pathlib import Path
from typing import Annotated, Optional
import typer

from .tmux_driver import TUIDriver
from .renderer import render_terminal_to_png

app = typer.Typer(
    name="tui-eye",
    help="Visual TUI testing tool - gives Claude Code 'eyes' into TUI apps",
)

# Global driver instance (persists between commands via state file)
STATE_FILE = Path("/tmp/tui-eye-state")
DEFAULT_SESSION = "tui-eye"


def _get_driver() -> TUIDriver:
    """Get or create a TUI driver instance."""
    session_name = DEFAULT_SESSION
    if STATE_FILE.exists():
        session_name = STATE_FILE.read_text().strip() or DEFAULT_SESSION
    return TUIDriver(session_name=session_name)


def _save_state(session_name: str) -> None:
    """Save the current session name to state file."""
    STATE_FILE.write_text(session_name)


def _clear_state() -> None:
    """Clear the state file."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


@app.command()
def start(
    command: Annotated[str, typer.Argument(help="Command to run in the TUI")],
    size: Annotated[str, typer.Option(help="Terminal size as WIDTHxHEIGHT")] = "220x40",
    session: Annotated[str, typer.Option(help="tmux session name")] = DEFAULT_SESSION,
) -> None:
    """Start a TUI application in a tmux session."""
    # Parse size
    try:
        width, height = map(int, size.lower().split("x"))
    except ValueError:
        typer.echo(f"Error: Invalid size format '{size}'. Use WIDTHxHEIGHT (e.g., 120x40)")
        raise typer.Exit(1)

    driver = TUIDriver(session_name=session)

    typer.echo(f"Starting TUI: {command}")
    typer.echo(f"Size: {width}x{height}")
    typer.echo(f"Session: {session}")

    driver.start(command, width=width, height=height)
    _save_state(session)

    typer.echo("TUI started. Use 'tui-eye screenshot' to capture.")


@app.command()
def stop() -> None:
    """Stop the TUI session and clean up."""
    driver = _get_driver()
    driver.stop()
    _clear_state()
    typer.echo("TUI session stopped.")


@app.command()
def screenshot(
    output: Annotated[
        str, typer.Argument(help="Output PNG file path")
    ] = "/tmp/tui-screenshot.png",
    width: Annotated[int, typer.Option(help="Terminal width for rendering")] = 220,
    height: Annotated[int, typer.Option(help="Terminal height for rendering")] = 45,
) -> None:
    """Capture a screenshot of the TUI as a PNG image."""
    driver = _get_driver()

    if not driver.is_running:
        typer.echo("Error: No TUI session running. Use 'tui-eye start' first.")
        raise typer.Exit(1)

    # Capture with ANSI codes
    content = driver.capture(with_ansi=True)

    # Render to PNG
    output_path = render_terminal_to_png(
        content,
        output,
        width=width,
        height=height,
    )

    typer.echo(f"Screenshot saved: {output_path}")


@app.command()
def capture(
    text: Annotated[bool, typer.Option("--text", help="Output plain text (no ANSI)")] = False,
) -> None:
    """Capture and print the current screen content."""
    driver = _get_driver()

    if not driver.is_running:
        typer.echo("Error: No TUI session running. Use 'tui-eye start' first.")
        raise typer.Exit(1)

    content = driver.capture(with_ansi=not text)
    typer.echo(content)


@app.command()
def send(
    keys: Annotated[list[str], typer.Argument(help="Keys to send (e.g., j k enter)")],
) -> None:
    """Send keystrokes to the TUI."""
    driver = _get_driver()

    if not driver.is_running:
        typer.echo("Error: No TUI session running. Use 'tui-eye start' first.")
        raise typer.Exit(1)

    driver.send_keys(*keys)
    typer.echo(f"Sent keys: {' '.join(keys)}")


@app.command("wait-for")
def wait_for(
    text: Annotated[str, typer.Argument(help="Text to wait for")],
    timeout: Annotated[float, typer.Option(help="Timeout in seconds")] = 10.0,
) -> None:
    """Wait for specific text to appear on screen."""
    driver = _get_driver()

    if not driver.is_running:
        typer.echo("Error: No TUI session running. Use 'tui-eye start' first.")
        raise typer.Exit(1)

    typer.echo(f"Waiting for: '{text}' (timeout: {timeout}s)")

    if driver.wait_for(text, timeout=timeout):
        typer.echo("Found!")
    else:
        typer.echo(f"Timeout: text '{text}' not found after {timeout}s")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Check the status of the TUI session."""
    driver = _get_driver()

    if driver.is_running:
        typer.echo(f"Session '{driver.session_name}' is running.")
    else:
        typer.echo("No TUI session running.")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
