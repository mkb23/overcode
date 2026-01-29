"""
Unit tests for PresenceLogger.

These tests focus on the pure logic functions that don't require
macOS-specific APIs.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.presence_logger import (
    infer_sleep,
    classify_state,
    state_to_name,
    PresenceLoggerConfig,
    PresenceLogger,
    read_presence_history,
    DEFAULT_SAMPLE_INTERVAL,
    DEFAULT_IDLE_THRESHOLD,
)


class TestInferSleep:
    """Test sleep inference logic"""

    def test_no_last_timestamp(self):
        """No previous timestamp means no sleep inferred"""
        now = datetime.now()
        assert infer_sleep(None, now, 60) is False

    def test_normal_gap_no_sleep(self):
        """Normal gap doesn't trigger sleep inference"""
        now = datetime.now()
        last = now - timedelta(seconds=60)  # 1 minute ago

        assert infer_sleep(last, now, 60) is False

    def test_double_interval_triggers_sleep(self):
        """Gap > 2x interval triggers sleep inference"""
        now = datetime.now()
        last = now - timedelta(seconds=150)  # 2.5 minutes ago

        assert infer_sleep(last, now, 60) is True

    def test_exactly_double_no_sleep(self):
        """Gap exactly 2x interval doesn't trigger"""
        now = datetime.now()
        last = now - timedelta(seconds=120)  # Exactly 2 minutes

        assert infer_sleep(last, now, 60) is False

    def test_large_gap_triggers_sleep(self):
        """Very large gap (e.g., laptop slept) triggers inference"""
        now = datetime.now()
        last = now - timedelta(hours=1)  # 1 hour ago

        assert infer_sleep(last, now, 60) is True


class TestClassifyState:
    """Test state classification logic"""

    def test_locked_returns_state_1(self):
        """Locked screen returns state 1"""
        assert classify_state(locked=True, idle_seconds=0, slept=False, idle_threshold=60) == 1

    def test_slept_returns_state_1(self):
        """Inferred sleep returns state 1"""
        assert classify_state(locked=False, idle_seconds=0, slept=True, idle_threshold=60) == 1

    def test_idle_over_threshold_returns_state_2(self):
        """Idle over threshold returns state 2"""
        assert classify_state(locked=False, idle_seconds=120, slept=False, idle_threshold=60) == 2

    def test_active_returns_state_3(self):
        """Active user (low idle) returns state 3"""
        assert classify_state(locked=False, idle_seconds=30, slept=False, idle_threshold=60) == 3

    def test_locked_takes_priority(self):
        """Locked takes priority over idle time"""
        assert classify_state(locked=True, idle_seconds=30, slept=False, idle_threshold=60) == 1

    def test_slept_takes_priority(self):
        """Slept takes priority over idle time"""
        assert classify_state(locked=False, idle_seconds=30, slept=True, idle_threshold=60) == 1

    def test_exactly_at_threshold(self):
        """At exactly threshold returns state 3 (not over)"""
        assert classify_state(locked=False, idle_seconds=60, slept=False, idle_threshold=60) == 3

    def test_just_over_threshold(self):
        """Just over threshold returns state 2"""
        assert classify_state(locked=False, idle_seconds=61, slept=False, idle_threshold=60) == 2


class TestStateToName:
    """Test state name conversion"""

    def test_state_1_name(self):
        """State 1 is locked/sleep"""
        assert state_to_name(1) == "locked/sleep"

    def test_state_2_name(self):
        """State 2 is inactive"""
        assert state_to_name(2) == "inactive"

    def test_state_3_name(self):
        """State 3 is active"""
        assert state_to_name(3) == "active"

    def test_unknown_state(self):
        """Unknown state returns unknown"""
        assert state_to_name(99) == "unknown"


class TestPresenceLoggerConfig:
    """Test configuration dataclass"""

    def test_default_values(self):
        """Config has sensible defaults"""
        config = PresenceLoggerConfig()

        assert config.sample_interval == DEFAULT_SAMPLE_INTERVAL
        assert config.idle_threshold == DEFAULT_IDLE_THRESHOLD
        assert config.log_path != ""  # Should have default path

    def test_custom_values(self):
        """Can override defaults"""
        config = PresenceLoggerConfig(
            sample_interval=30,
            idle_threshold=120,
            log_path="/custom/path.csv"
        )

        assert config.sample_interval == 30
        assert config.idle_threshold == 120
        assert config.log_path == "/custom/path.csv"

    def test_log_path_defaults_if_empty(self):
        """Empty log_path gets default in __post_init__"""
        config = PresenceLoggerConfig(log_path="")
        assert ".overcode" in config.log_path
        assert "presence_log.csv" in config.log_path


class TestHelperFunctions:
    """Test module-level helper functions."""

    def test_default_log_path_creates_directory(self, tmp_path, monkeypatch):
        """default_log_path should create the directory."""
        from overcode.presence_logger import default_log_path, OVERCODE_DIR

        path = default_log_path()
        assert ".overcode" in path
        assert "presence_log.csv" in path

    def test_is_presence_running(self, tmp_path, monkeypatch):
        """is_presence_running should check PID file."""
        from overcode import presence_logger

        # Patch the PID file path
        pid_file = tmp_path / "presence.pid"
        monkeypatch.setattr(presence_logger, 'PRESENCE_PID_FILE', pid_file)

        # No file = not running
        assert presence_logger.is_presence_running() is False

        # File with our PID = running
        import os
        pid_file.write_text(str(os.getpid()))
        assert presence_logger.is_presence_running() is True

        # File with dead PID = not running
        pid_file.write_text("99999999")
        assert presence_logger.is_presence_running() is False

    def test_get_presence_pid(self, tmp_path, monkeypatch):
        """get_presence_pid should return PID or None."""
        from overcode import presence_logger
        import os

        pid_file = tmp_path / "presence.pid"
        monkeypatch.setattr(presence_logger, 'PRESENCE_PID_FILE', pid_file)

        # No file = None
        assert presence_logger.get_presence_pid() is None

        # Valid PID
        pid_file.write_text(str(os.getpid()))
        assert presence_logger.get_presence_pid() == os.getpid()


class TestReadPresenceHistory:
    """Test read_presence_history function."""

    def test_returns_empty_for_missing_file(self, tmp_path, monkeypatch):
        """Should return empty list when file doesn't exist."""
        from overcode import presence_logger

        monkeypatch.setattr(presence_logger, 'default_log_path', lambda: str(tmp_path / "missing.csv"))
        result = read_presence_history(hours=1.0)

        assert result == []

    def test_reads_csv_data(self, tmp_path, monkeypatch):
        """Should read and parse CSV data."""
        from overcode import presence_logger
        import datetime as dt

        log_file = tmp_path / "presence.csv"
        now = dt.datetime.now()

        # Write test data
        with open(log_file, 'w') as f:
            f.write("timestamp,state,idle_seconds,locked,inferred_sleep\n")
            f.write(f"{now.isoformat()},3,10.5,0,0\n")

        monkeypatch.setattr(presence_logger, 'default_log_path', lambda: str(log_file))
        result = read_presence_history(hours=1.0)

        assert len(result) == 1
        assert result[0][1] == 3  # state

    def test_filters_by_time_window(self, tmp_path, monkeypatch):
        """Should filter entries within the time window."""
        from overcode import presence_logger
        import datetime as dt

        log_file = tmp_path / "presence.csv"
        now = dt.datetime.now()
        old_time = now - dt.timedelta(hours=5)  # Too old

        with open(log_file, 'w') as f:
            f.write("timestamp,state,idle_seconds,locked,inferred_sleep\n")
            f.write(f"{now.isoformat()},3,10.5,0,0\n")  # Recent
            f.write(f"{old_time.isoformat()},2,60.0,0,0\n")  # Too old

        monkeypatch.setattr(presence_logger, 'default_log_path', lambda: str(log_file))
        result = read_presence_history(hours=1.0)

        assert len(result) == 1  # Only recent entry


class TestPresenceLogger:
    """Test PresenceLogger class."""

    def test_init_default_config(self):
        """Should initialize with default config."""
        from overcode.presence_logger import PresenceLogger, PresenceLoggerConfig

        logger = PresenceLogger()

        assert logger.config.sample_interval == DEFAULT_SAMPLE_INTERVAL
        assert logger.config.idle_threshold == DEFAULT_IDLE_THRESHOLD

    def test_init_custom_config(self):
        """Should accept custom config."""
        from overcode.presence_logger import PresenceLogger, PresenceLoggerConfig

        config = PresenceLoggerConfig(sample_interval=30, idle_threshold=90)
        logger = PresenceLogger(config)

        assert logger.config.sample_interval == 30
        assert logger.config.idle_threshold == 90

    def test_start_stop(self, tmp_path):
        """Should start and stop cleanly."""
        from overcode.presence_logger import PresenceLogger, PresenceLoggerConfig

        config = PresenceLoggerConfig(
            sample_interval=1,
            log_path=str(tmp_path / "test.csv"),
        )
        logger = PresenceLogger(config)

        # Start should not block
        logger.start()
        # Stop should be quick
        logger.stop(timeout=2.0)

    def test_get_current_state_returns_tuple(self, tmp_path):
        """get_current_state should return state tuple."""
        from overcode.presence_logger import PresenceLogger, PresenceLoggerConfig

        config = PresenceLoggerConfig(
            sample_interval=60,
            log_path=str(tmp_path / "test.csv"),
        )
        logger = PresenceLogger(config)

        state, idle, locked = logger.get_current_state()

        # Should return valid values (may not be accurate without macOS APIs)
        assert isinstance(state, int)
        assert isinstance(idle, float)
        assert isinstance(locked, bool)


class TestSingletonFunctions:
    """Test singleton logger functions."""

    def test_get_singleton_logger_returns_none_initially(self):
        """Should return None when no logger started."""
        from overcode import presence_logger

        # Reset singleton state
        with presence_logger._singleton_lock:
            presence_logger._singleton_logger = None

        result = presence_logger.get_singleton_logger()

        assert result is None


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
