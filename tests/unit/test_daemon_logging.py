"""Tests for daemon_logging module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import StringIO

from overcode.daemon_logging import (
    BaseDaemonLogger,
    SupervisorDaemonLogger,
    DAEMON_THEME,
)


class TestBaseDaemonLogger:
    """Tests for BaseDaemonLogger class."""

    def test_creates_log_directory(self, tmp_path):
        """Should create parent directory for log file."""
        log_file = tmp_path / "subdir" / "test.log"
        logger = BaseDaemonLogger(log_file)

        assert log_file.parent.exists()

    def test_write_to_file(self, tmp_path):
        """Should write messages to log file."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger._write_to_file("Test message", "INFO")

        content = log_file.read_text()
        assert "Test message" in content
        assert "[INFO]" in content

    def test_write_to_file_handles_oserror(self, tmp_path):
        """Should handle OSError when writing to file."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        # Make log file a directory to cause OSError
        log_file.mkdir()

        # Should not raise
        logger._write_to_file("Test message", "INFO")

    def test_log_method(self, tmp_path):
        """Should log with style to console and file."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger._log("info", "●", "Test log message", "INFO")

        content = log_file.read_text()
        assert "Test log message" in content

    def test_info(self, tmp_path):
        """Should log info messages."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.info("Info message")

        content = log_file.read_text()
        assert "Info message" in content
        assert "[INFO]" in content

    def test_warn(self, tmp_path):
        """Should log warning messages."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.warn("Warning message")

        content = log_file.read_text()
        assert "Warning message" in content
        assert "[WARN]" in content

    def test_error(self, tmp_path):
        """Should log error messages."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.error("Error message")

        content = log_file.read_text()
        assert "Error message" in content
        assert "[ERROR]" in content

    def test_success(self, tmp_path):
        """Should log success messages."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.success("Success message")

        content = log_file.read_text()
        assert "Success message" in content
        assert "[INFO]" in content  # Success uses INFO level

    def test_debug(self, tmp_path):
        """Should log debug messages only to file."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.debug("Debug message")

        content = log_file.read_text()
        assert "Debug message" in content
        assert "DEBUG" in content

    def test_debug_handles_oserror(self, tmp_path):
        """Should handle OSError in debug logging."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        # Make log file a directory to cause OSError
        log_file.mkdir()

        # Should not raise
        logger.debug("Debug message")

    def test_section(self, tmp_path):
        """Should print section header."""
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file)

        logger.section("Test Section")

        content = log_file.read_text()
        assert "Test Section" in content

    def test_custom_theme(self, tmp_path):
        """Should accept custom theme."""
        from rich.theme import Theme

        custom_theme = Theme({"custom": "bold blue"})
        log_file = tmp_path / "test.log"
        logger = BaseDaemonLogger(log_file, theme=custom_theme)

        # Verify the logger was created with custom theme (theme is used internally)
        assert logger.console is not None


class TestSupervisorDaemonLogger:
    """Tests for SupervisorDaemonLogger class."""

    def test_inherits_from_base(self, tmp_path):
        """Should inherit from BaseDaemonLogger."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        assert isinstance(logger, BaseDaemonLogger)

    def test_daemon_claude_output_empty(self, tmp_path):
        """Should handle empty output."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output([])

        # No output should be written
        content = log_file.read_text() if log_file.exists() else ""
        assert "[DAEMON_CLAUDE]" not in content

    def test_daemon_claude_output_new_lines(self, tmp_path):
        """Should log new lines from daemon claude."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["Line 1", "Line 2"])

        content = log_file.read_text()
        assert "[DAEMON_CLAUDE] Line 1" in content
        assert "[DAEMON_CLAUDE] Line 2" in content

    def test_daemon_claude_output_skips_duplicates(self, tmp_path):
        """Should skip duplicate lines."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["Line 1", "Line 2"])
        logger.daemon_claude_output(["Line 1", "Line 3"])  # Line 1 is duplicate

        content = log_file.read_text()
        # Line 1 should appear only once
        assert content.count("[DAEMON_CLAUDE] Line 1") == 1
        assert "[DAEMON_CLAUDE] Line 3" in content

    def test_daemon_claude_output_skips_empty_lines(self, tmp_path):
        """Should skip empty lines."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["Line 1", "", "   ", "Line 2"])

        content = log_file.read_text()
        assert "[DAEMON_CLAUDE] Line 1" in content
        assert "[DAEMON_CLAUDE] Line 2" in content
        # Should not have empty entries
        assert "[DAEMON_CLAUDE] \n" not in content

    def test_daemon_claude_output_limits_seen_lines(self, tmp_path):
        """Should limit size of seen lines set."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        # Add 600 unique lines
        lines = [f"Line {i}" for i in range(600)]
        logger.daemon_claude_output(lines)

        # Add more lines to trigger cleanup
        new_lines = ["New line 1", "New line 2"]
        logger.daemon_claude_output(new_lines)

        # The set should be limited
        assert len(logger._seen_daemon_claude_lines) <= 502  # current + new

    def test_daemon_claude_output_success_styling(self, tmp_path):
        """Should style success lines differently."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["✓ Task completed"])

        content = log_file.read_text()
        assert "✓ Task completed" in content

    def test_daemon_claude_output_error_styling(self, tmp_path):
        """Should style error lines differently."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["✗ Task failed", "error occurred", "fail happened"])

        content = log_file.read_text()
        assert "✗ Task failed" in content
        assert "error occurred" in content
        assert "fail happened" in content

    def test_daemon_claude_output_command_styling(self, tmp_path):
        """Should style command lines differently."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.daemon_claude_output(["> Running command", "$ ls -la"])

        content = log_file.read_text()
        assert "> Running command" in content
        assert "$ ls -la" in content

    def test_status_summary(self, tmp_path):
        """Should print status summary."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.status_summary(total=5, green=3, non_green=2, loop=10)

        content = log_file.read_text()
        assert "Loop #10" in content
        assert "5 agents" in content
        assert "3 green" in content
        assert "2 non-green" in content

    def test_status_summary_zero_non_green(self, tmp_path):
        """Should handle zero non-green agents."""
        log_file = tmp_path / "test.log"
        logger = SupervisorDaemonLogger(log_file)

        logger.status_summary(total=3, green=3, non_green=0, loop=1)

        content = log_file.read_text()
        assert "3 agents" in content
        assert "0 non-green" in content


class TestDaemonTheme:
    """Tests for DAEMON_THEME."""

    def test_theme_has_required_styles(self):
        """Should have all required styles defined."""
        required_styles = ["info", "warn", "error", "success", "daemon_claude", "dim", "highlight"]

        for style in required_styles:
            assert style in DAEMON_THEME.styles
