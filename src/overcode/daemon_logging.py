"""
Shared logging utilities for Overcode daemons.

Provides base logger class with common functionality for both
monitor_daemon and supervisor_daemon.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.text import Text
from rich.theme import Theme


# Shared theme for daemon logs
DAEMON_THEME = Theme({
    "info": "cyan",
    "warn": "yellow",
    "error": "bold red",
    "success": "bold green",
    "daemon_claude": "magenta",
    "dim": "dim white",
    "highlight": "bold white",
})


class BaseDaemonLogger:
    """Base logger for daemons with common logging methods."""

    def __init__(self, log_file: Path, theme: Theme = None):
        """Initialize the logger.

        Args:
            log_file: Path to the log file
            theme: Optional Rich theme (defaults to DAEMON_THEME)
        """
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.console = Console(theme=theme or DAEMON_THEME, force_terminal=True)

    def _write_to_file(self, message: str, level: str = "INFO"):
        """Write plain text to log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"
        try:
            with open(self.log_file, 'a') as f:
                f.write(line + '\n')
        except OSError:
            pass

    def _log(self, style: str, prefix: str, message: str, level: str = "INFO"):
        """Log a message with style to both console and file."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append(f"{prefix} ", style=style)
        text.append(message)
        self.console.print(text)
        self._write_to_file(message, level)

    def info(self, message: str):
        """Log info message."""
        self._log("info", "●", message, "INFO")

    def warn(self, message: str):
        """Log warning message."""
        self._log("warn", "⚠", message, "WARN")

    def error(self, message: str):
        """Log error message."""
        self._log("error", "✗", message, "ERROR")

    def success(self, message: str):
        """Log success message."""
        self._log("success", "✓", message, "INFO")

    def debug(self, message: str):
        """Log a debug message (only to file, not console)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.log_file, 'a') as f:
                f.write(f"[{timestamp}] DEBUG {message}\n")
        except OSError:
            pass

    def section(self, title: str):
        """Print a section header."""
        self._write_to_file(f"=== {title} ===", "INFO")
        self.console.print()
        self.console.rule(f"[bold cyan]{title}[/]")


class SupervisorDaemonLogger(BaseDaemonLogger):
    """Logger for supervisor daemon with additional methods."""

    def __init__(self, log_file: Path):
        super().__init__(log_file)
        self._seen_daemon_claude_lines: set = set()

    def daemon_claude_output(self, lines: List[str]):
        """Log daemon claude output, showing only new lines."""
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped not in self._seen_daemon_claude_lines:
                new_lines.append(stripped)
                self._seen_daemon_claude_lines.add(stripped)

        # Limit set size
        if len(self._seen_daemon_claude_lines) > 500:
            current_lines = {line.strip() for line in lines if line.strip()}
            self._seen_daemon_claude_lines = current_lines

        if new_lines:
            for line in new_lines:
                self._write_to_file(f"[DAEMON_CLAUDE] {line}", "INFO")
                if line.startswith('✓') or 'success' in line.lower():
                    self.console.print(f"  [success]│[/success] {line}")
                elif line.startswith('✗') or 'error' in line.lower() or 'fail' in line.lower():
                    self.console.print(f"  [error]│[/error] {line}")
                elif line.startswith('>') or line.startswith('$'):
                    self.console.print(f"  [highlight]│[/highlight] {line}")
                else:
                    self.console.print(f"  [daemon_claude]│[/daemon_claude] {line}")

    def status_summary(self, total: int, green: int, non_green: int, loop: int):
        """Print a status summary line."""
        status_text = Text()
        status_text.append(f"Loop #{loop}: ", style="dim")
        status_text.append(f"{total} agents ", style="highlight")
        status_text.append("(", style="dim")
        status_text.append(f"{green} green", style="success")
        status_text.append(", ", style="dim")
        status_text.append(f"{non_green} non-green", style="warn" if non_green else "dim")
        status_text.append(")", style="dim")

        self._write_to_file(f"Loop #{loop}: {total} agents ({green} green, {non_green} non-green)", "INFO")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ", end="")
        self.console.print(status_text)
