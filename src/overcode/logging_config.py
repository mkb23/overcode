"""
Structured logging configuration for Overcode.

Provides centralized logging configuration with support for:
- Console output with Rich formatting (optional)
- File output for persistent logs
- Different log levels per component
- Structured log messages
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# Default log directory
DEFAULT_LOG_DIR = Path.home() / ".overcode" / "logs"


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the specified component.

    Args:
        name: Component name (e.g., 'daemon', 'launcher', 'tui')

    Returns:
        Configured logger instance
    """
    return logging.getLogger(f"overcode.{name}")


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    console: bool = True,
    rich_console: bool = False,
) -> None:
    """Configure logging for the application.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional path to log file
        console: Whether to log to console (default: True)
        rich_console: Whether to use Rich for console output (default: False)
    """
    root_logger = logging.getLogger("overcode")
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Log format
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Console handler
    if console:
        if rich_console:
            try:
                from rich.logging import RichHandler

                console_handler = RichHandler(
                    show_time=True,
                    show_path=False,
                    markup=True,
                    rich_tracebacks=True,
                )
                console_handler.setLevel(level)
                root_logger.addHandler(console_handler)
            except ImportError:
                # Fall back to standard console handler
                console_handler = logging.StreamHandler(sys.stderr)
                console_handler.setLevel(level)
                console_handler.setFormatter(logging.Formatter(fmt, date_fmt))
                root_logger.addHandler(console_handler)
        else:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(level)
            console_handler.setFormatter(logging.Formatter(fmt, date_fmt))
            root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(fmt, date_fmt))
        root_logger.addHandler(file_handler)


def setup_daemon_logging(log_file: Optional[Path] = None) -> logging.Logger:
    """Configure logging specifically for the daemon.

    Uses file logging by default to the daemon log directory.

    Args:
        log_file: Optional custom log file path

    Returns:
        Configured daemon logger
    """
    if log_file is None:
        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = DEFAULT_LOG_DIR / "daemon.log"

    setup_logging(
        level=logging.INFO,
        log_file=log_file,
        console=True,
        rich_console=True,
    )

    return get_logger("daemon")


def setup_cli_logging() -> logging.Logger:
    """Configure logging for CLI commands.

    Uses minimal console output since CLI uses Rich for user feedback.

    Returns:
        Configured CLI logger
    """
    setup_logging(
        level=logging.WARNING,  # Only warnings and errors
        console=True,
        rich_console=False,
    )

    return get_logger("cli")


class StructuredLogger:
    """Logger that supports structured log messages with context."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._context: dict = {}

    def with_context(self, **kwargs) -> "StructuredLogger":
        """Create a new logger with additional context.

        Args:
            **kwargs: Key-value pairs to add to log context

        Returns:
            New StructuredLogger with merged context
        """
        new_logger = StructuredLogger(self._logger)
        new_logger._context = {**self._context, **kwargs}
        return new_logger

    def _format_message(self, message: str, **kwargs) -> str:
        """Format message with context."""
        context = {**self._context, **kwargs}
        if context:
            context_str = " ".join(f"{k}={v}" for k, v in context.items())
            return f"{message} [{context_str}]"
        return message

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self._logger.debug(self._format_message(message, **kwargs))

    def info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self._logger.info(self._format_message(message, **kwargs))

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self._logger.warning(self._format_message(message, **kwargs))

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self._logger.error(self._format_message(message, **kwargs))

    def exception(self, message: str, **kwargs) -> None:
        """Log exception with traceback."""
        self._logger.exception(self._format_message(message, **kwargs))


def get_structured_logger(name: str) -> StructuredLogger:
    """Get a structured logger for the specified component.

    Args:
        name: Component name

    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(get_logger(name))
