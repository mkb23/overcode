"""
Claude Code subscription usage monitor.

Fetches 5-hour session and 7-day weekly usage limits from the Anthropic API.
Self-contained module: all failures produce UsageSnapshot(error=...), never raises.
"""

import json
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable snapshot of Claude Code usage data."""
    five_hour_pct: float = 0.0
    seven_day_pct: float = 0.0
    five_hour_resets_at: Optional[str] = None
    seven_day_resets_at: Optional[str] = None
    opus_pct: Optional[float] = None
    sonnet_pct: Optional[float] = None
    fetched_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None


class UsageMonitor:
    """Monitors Claude Code subscription usage via the Anthropic API.

    Caches results and throttles fetches to a minimum interval (default 90s).
    """

    def __init__(self):
        self._snapshot: Optional[UsageSnapshot] = None
        self._last_fetch_time: float = 0
        self._min_interval: int = 90

    @property
    def snapshot(self) -> Optional[UsageSnapshot]:
        """Return the cached usage snapshot (no I/O)."""
        return self._snapshot

    def fetch(self) -> None:
        """Fetch usage data, throttled to _min_interval seconds.

        Safe to call frequently; will skip if called too soon.
        """
        now = time.time()
        if now - self._last_fetch_time < self._min_interval:
            return

        self._last_fetch_time = now

        token = self._get_access_token()
        if token is None:
            self._snapshot = UsageSnapshot(
                error="no token",
                fetched_at=datetime.now(),
            )
            return

        self._snapshot = self._fetch_usage(token)

    @staticmethod
    def _get_access_token() -> Optional[str]:
        """Retrieve the Claude Code OAuth access token from macOS Keychain."""
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            raw = result.stdout.strip()
            data = json.loads(raw)
            return data.get("claudeAiOauth", {}).get("accessToken")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, OSError):
            return None

    @staticmethod
    def _fetch_usage(token: str) -> UsageSnapshot:
        """Fetch usage data from the Anthropic API."""
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            five_hour = data.get("five_hour", {})
            seven_day = data.get("seven_day", {})
            opus = data.get("seven_day_opus")
            sonnet = data.get("seven_day_sonnet")

            return UsageSnapshot(
                five_hour_pct=five_hour.get("utilization", 0.0),
                seven_day_pct=seven_day.get("utilization", 0.0),
                five_hour_resets_at=five_hour.get("resets_at"),
                seven_day_resets_at=seven_day.get("resets_at"),
                opus_pct=opus.get("utilization") if opus else None,
                sonnet_pct=sonnet.get("utilization") if sonnet else None,
                fetched_at=datetime.now(),
            )
        except (urllib.error.URLError, json.JSONDecodeError, OSError, KeyError) as e:
            return UsageSnapshot(
                error=str(e),
                fetched_at=datetime.now(),
            )
