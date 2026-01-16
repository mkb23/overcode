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


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
