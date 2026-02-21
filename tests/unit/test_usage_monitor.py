"""Tests for usage_monitor module."""

import json
import time
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from urllib.error import URLError

from overcode.usage_monitor import UsageMonitor, UsageSnapshot


class TestUsageSnapshot:
    """Tests for UsageSnapshot dataclass."""

    def test_defaults(self):
        s = UsageSnapshot()
        assert s.five_hour_pct == 0.0
        assert s.seven_day_pct == 0.0
        assert s.five_hour_resets_at is None
        assert s.seven_day_resets_at is None
        assert s.opus_pct is None
        assert s.sonnet_pct is None
        assert s.error is None
        assert isinstance(s.fetched_at, datetime)

    def test_frozen(self):
        s = UsageSnapshot()
        with pytest.raises(AttributeError):
            s.five_hour_pct = 1.0

    def test_with_values(self):
        s = UsageSnapshot(
            five_hour_pct=0.5,
            seven_day_pct=0.3,
            five_hour_resets_at="2024-01-01T12:00:00",
            seven_day_resets_at="2024-01-07T12:00:00",
            opus_pct=0.8,
            sonnet_pct=0.2,
        )
        assert s.five_hour_pct == 0.5
        assert s.opus_pct == 0.8

    def test_error_snapshot(self):
        s = UsageSnapshot(error="no token")
        assert s.error == "no token"


class TestUsageMonitor:
    """Tests for UsageMonitor class."""

    def test_initial_snapshot_is_none(self):
        m = UsageMonitor()
        assert m.snapshot is None

    def test_fetch_throttling(self):
        m = UsageMonitor()
        m._min_interval = 999
        m._last_fetch_time = time.time()

        with patch.object(m, "_get_access_token") as mock_token:
            m.fetch()
            mock_token.assert_not_called()

    def test_fetch_no_token(self):
        m = UsageMonitor()
        m._last_fetch_time = 0

        with patch.object(UsageMonitor, "_get_access_token", return_value=None):
            m.fetch()

        assert m.snapshot is not None
        assert m.snapshot.error == "no token"

    def test_fetch_with_token(self):
        m = UsageMonitor()
        m._last_fetch_time = 0
        expected = UsageSnapshot(five_hour_pct=0.5, seven_day_pct=0.3)

        with patch.object(UsageMonitor, "_get_access_token", return_value="test-token"):
            with patch.object(UsageMonitor, "_fetch_usage", return_value=expected):
                m.fetch()

        assert m.snapshot is expected

    def test_fetch_updates_last_fetch_time(self):
        m = UsageMonitor()
        m._last_fetch_time = 0

        with patch.object(UsageMonitor, "_get_access_token", return_value=None):
            m.fetch()

        assert m._last_fetch_time > 0


class TestGetAccessToken:
    """Tests for _get_access_token static method."""

    def test_returns_token_on_success(self):
        creds = json.dumps({"claudeAiOauth": {"accessToken": "test-abc"}})
        result = MagicMock()
        result.returncode = 0
        result.stdout = creds

        with patch("overcode.usage_monitor.subprocess.run", return_value=result):
            token = UsageMonitor._get_access_token()

        assert token == "test-abc"

    def test_returns_none_on_nonzero_exit(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""

        with patch("overcode.usage_monitor.subprocess.run", return_value=result):
            token = UsageMonitor._get_access_token()

        assert token is None

    def test_returns_none_on_invalid_json(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "not json"

        with patch("overcode.usage_monitor.subprocess.run", return_value=result):
            token = UsageMonitor._get_access_token()

        assert token is None

    def test_returns_none_on_timeout(self):
        import subprocess
        with patch("overcode.usage_monitor.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="security", timeout=5)):
            token = UsageMonitor._get_access_token()

        assert token is None

    def test_returns_none_on_missing_key(self):
        creds = json.dumps({"other": "data"})
        result = MagicMock()
        result.returncode = 0
        result.stdout = creds

        with patch("overcode.usage_monitor.subprocess.run", return_value=result):
            token = UsageMonitor._get_access_token()

        assert token is None


class TestFetchUsage:
    """Tests for _fetch_usage static method."""

    def test_successful_fetch(self):
        api_response = json.dumps({
            "five_hour": {"utilization": 0.42, "resets_at": "2024-01-01T15:00:00"},
            "seven_day": {"utilization": 0.18, "resets_at": "2024-01-07T00:00:00"},
            "seven_day_opus": {"utilization": 0.6},
            "seven_day_sonnet": {"utilization": 0.1},
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.usage_monitor.urllib.request.urlopen", return_value=mock_resp):
            snap = UsageMonitor._fetch_usage("test-token")

        assert snap.five_hour_pct == 0.42
        assert snap.seven_day_pct == 0.18
        assert snap.opus_pct == 0.6
        assert snap.sonnet_pct == 0.1
        assert snap.error is None

    def test_fetch_without_model_breakdown(self):
        api_response = json.dumps({
            "five_hour": {"utilization": 0.5},
            "seven_day": {"utilization": 0.2},
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.usage_monitor.urllib.request.urlopen", return_value=mock_resp):
            snap = UsageMonitor._fetch_usage("test-token")

        assert snap.opus_pct is None
        assert snap.sonnet_pct is None

    def test_fetch_url_error(self):
        with patch("overcode.usage_monitor.urllib.request.urlopen",
                   side_effect=URLError("connection refused")):
            snap = UsageMonitor._fetch_usage("test-token")

        assert snap.error is not None
        assert "connection refused" in snap.error

    def test_fetch_json_error(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.usage_monitor.urllib.request.urlopen", return_value=mock_resp):
            snap = UsageMonitor._fetch_usage("test-token")

        assert snap.error is not None
