"""Tests for logging_config module."""

import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.logging_config import (
    get_logger,
    setup_logging,
    setup_daemon_logging,
    setup_cli_logging,
    StructuredLogger,
    get_structured_logger,
    DEFAULT_LOG_DIR,
)


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration after each test."""
    yield
    # Clear all handlers from overcode logger
    logger = logging.getLogger("overcode")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_logger(self):
        logger = get_logger("test")
        assert isinstance(logger, logging.Logger)

    def test_logger_name_prefixed(self):
        logger = get_logger("mycomponent")
        assert logger.name == "overcode.mycomponent"

    def test_different_names_return_different_loggers(self):
        logger1 = get_logger("component1")
        logger2 = get_logger("component2")
        assert logger1 is not logger2
        assert logger1.name != logger2.name

    def test_same_name_returns_same_logger(self):
        logger1 = get_logger("same")
        logger2 = get_logger("same")
        assert logger1 is logger2


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_sets_level(self):
        setup_logging(level=logging.DEBUG, console=False)
        logger = logging.getLogger("overcode")
        assert logger.level == logging.DEBUG

    def test_creates_console_handler(self):
        setup_logging(level=logging.INFO, console=True)
        logger = logging.getLogger("overcode")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], logging.StreamHandler)

    def test_no_console_handler_when_disabled(self):
        setup_logging(level=logging.INFO, console=False)
        logger = logging.getLogger("overcode")
        assert len(logger.handlers) == 0

    def test_creates_file_handler(self, tmp_path):
        log_file = tmp_path / "test.log"
        setup_logging(level=logging.INFO, log_file=log_file, console=False)

        logger = logging.getLogger("overcode")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], logging.FileHandler)

    def test_file_handler_creates_directory(self, tmp_path):
        log_file = tmp_path / "nested" / "dir" / "test.log"
        setup_logging(level=logging.INFO, log_file=log_file, console=False)

        assert log_file.parent.exists()

    def test_both_handlers(self, tmp_path):
        log_file = tmp_path / "test.log"
        setup_logging(level=logging.INFO, log_file=log_file, console=True)

        logger = logging.getLogger("overcode")
        assert len(logger.handlers) == 2

    def test_clears_existing_handlers(self):
        logger = logging.getLogger("overcode")
        # Add a dummy handler
        logger.addHandler(logging.NullHandler())
        assert len(logger.handlers) == 1

        # Setup should clear it
        setup_logging(level=logging.INFO, console=False)
        assert len(logger.handlers) == 0

    def test_rich_console_handler_fallback(self):
        # Test that it falls back to StreamHandler when Rich is not available
        with patch.dict("sys.modules", {"rich.logging": None}):
            setup_logging(level=logging.INFO, console=True, rich_console=True)

            logger = logging.getLogger("overcode")
            assert len(logger.handlers) == 1
            assert isinstance(logger.handlers[0], logging.StreamHandler)


class TestSetupDaemonLogging:
    """Tests for setup_daemon_logging function."""

    def test_returns_logger(self, tmp_path):
        log_file = tmp_path / "daemon.log"
        logger = setup_daemon_logging(log_file=log_file)

        assert isinstance(logger, logging.Logger)
        assert logger.name == "overcode.daemon"

    def test_creates_log_file(self, tmp_path):
        log_file = tmp_path / "daemon.log"
        setup_daemon_logging(log_file=log_file)

        # Write something to trigger file creation
        logger = get_logger("daemon")
        logger.info("Test message")

        assert log_file.exists()

    def test_uses_default_log_dir_when_none(self):
        with patch.object(Path, "mkdir"):
            with patch("overcode.logging_config.setup_logging") as mock_setup:
                setup_daemon_logging(log_file=None)

                # Should have called setup_logging with default path
                mock_setup.assert_called_once()
                call_kwargs = mock_setup.call_args[1]
                assert call_kwargs["log_file"] == DEFAULT_LOG_DIR / "daemon.log"


class TestSetupCliLogging:
    """Tests for setup_cli_logging function."""

    def test_returns_logger(self):
        logger = setup_cli_logging()

        assert isinstance(logger, logging.Logger)
        assert logger.name == "overcode.cli"

    def test_uses_warning_level(self):
        setup_cli_logging()

        logger = logging.getLogger("overcode")
        assert logger.level == logging.WARNING


class TestStructuredLogger:
    """Tests for StructuredLogger class."""

    @pytest.fixture
    def mock_logger(self):
        return MagicMock(spec=logging.Logger)

    @pytest.fixture
    def structured_logger(self, mock_logger):
        return StructuredLogger(mock_logger)

    def test_debug(self, structured_logger, mock_logger):
        structured_logger.debug("Test message")
        mock_logger.debug.assert_called_once_with("Test message")

    def test_info(self, structured_logger, mock_logger):
        structured_logger.info("Test message")
        mock_logger.info.assert_called_once_with("Test message")

    def test_warning(self, structured_logger, mock_logger):
        structured_logger.warning("Test message")
        mock_logger.warning.assert_called_once_with("Test message")

    def test_error(self, structured_logger, mock_logger):
        structured_logger.error("Test message")
        mock_logger.error.assert_called_once_with("Test message")

    def test_exception(self, structured_logger, mock_logger):
        structured_logger.exception("Test message")
        mock_logger.exception.assert_called_once_with("Test message")

    def test_with_context_returns_new_logger(self, structured_logger):
        new_logger = structured_logger.with_context(user="test")

        assert new_logger is not structured_logger
        assert isinstance(new_logger, StructuredLogger)

    def test_with_context_adds_context(self, mock_logger):
        logger = StructuredLogger(mock_logger)
        logger_with_context = logger.with_context(session_id="123")

        logger_with_context.info("Test")

        call_args = mock_logger.info.call_args[0][0]
        assert "session_id=123" in call_args

    def test_context_merges(self, mock_logger):
        logger = StructuredLogger(mock_logger)
        logger1 = logger.with_context(a="1")
        logger2 = logger1.with_context(b="2")

        logger2.info("Test")

        call_args = mock_logger.info.call_args[0][0]
        assert "a=1" in call_args
        assert "b=2" in call_args

    def test_inline_kwargs_added(self, mock_logger):
        logger = StructuredLogger(mock_logger)
        logger.info("Test", extra_key="value")

        call_args = mock_logger.info.call_args[0][0]
        assert "extra_key=value" in call_args

    def test_format_message_no_context(self, mock_logger):
        logger = StructuredLogger(mock_logger)
        logger.info("Plain message")

        mock_logger.info.assert_called_with("Plain message")

    def test_format_message_with_context_and_kwargs(self, mock_logger):
        logger = StructuredLogger(mock_logger)
        logger = logger.with_context(ctx="A")
        logger.info("Message", inline="B")

        call_args = mock_logger.info.call_args[0][0]
        assert "Message" in call_args
        assert "ctx=A" in call_args
        assert "inline=B" in call_args


class TestGetStructuredLogger:
    """Tests for get_structured_logger function."""

    def test_returns_structured_logger(self):
        logger = get_structured_logger("test")
        assert isinstance(logger, StructuredLogger)

    def test_underlying_logger_name(self):
        logger = get_structured_logger("component")
        # Access the underlying logger
        assert logger._logger.name == "overcode.component"
