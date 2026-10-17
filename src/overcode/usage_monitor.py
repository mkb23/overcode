"""
Claude Code subscription usage monitor.

Fetches 5-hour session and 7-day weekly usage limits from the Anthropic API.
Self-contained module: all failures produce UsageSnapshot(error=...), never raises.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


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

    Caches results and throttles fetches to a minimum interval (default 5min).
    """

    def __init__(self):
        self._snapshot: Optional[UsageSnapshot] = None
        self._last_fetch_time: float = 0
        self._min_interval: int = 300  # 5 minutes between API calls

    @property
    def snapshot(self) -> Optional[UsageSnapshot]:
        """Return the cached usage snapshot (no I/O)."""
        return self._snapshot

    def fetch(self) -> None:
        """Fetch usage data, throttled to _min_interval seconds.

        Safe to call frequently; will skip if called too soon.

        On fetch failure (rate limit, network error, missing token), keep
        the last successful snapshot instead of blanking it out. The UI's
        "Xm ago" marker grows naturally so users can still tell the data
        is stale. Only replace with an error snapshot if we have nothing
        good to fall back on.
        """
        now = time.time()
        if now - self._last_fetch_time < self._min_interval:
            return

        self._last_fetch_time = now

        token = self._get_access_token()
        if token is None:
            self._set_snapshot_on_error("no token")
            return

        new_snapshot = self._fetch_usage(token)
        if new_snapshot.error and self._snapshot and not self._snapshot.error:
            # Keep the last good snapshot so its fetched_at/values survive
            # a transient refresh failure (e.g. 429).
            return
        self._snapshot = new_snapshot

    def _set_snapshot_on_error(self, message: str) -> None:
        """Store an error snapshot only if we have no prior good data."""
        if self._snapshot and not self._snapshot.error:
            return
        self._snapshot = UsageSnapshot(
            error=message,
            fetched_at=datetime.now(),
        )

    @staticmethod
    def _get_access_token() -> Optional[str]:
        """Retrieve the Claude Code OAuth access token.

        Tries macOS Keychain first, then falls back to ~/.claude/.credentials.json
        (used on Linux and as a fallback on macOS).
        """
        creds = UsageMonitor.get_oauth_credentials()
        return creds.get("accessToken") if creds else None

    @staticmethod
    def get_oauth_credentials() -> Optional[dict]:
        """Claude Code's ``claudeAiOauth`` record (access token, and the
        ``subscriptionType`` / ``rateLimitTier`` the quota model's plan comes
        from), or None. Keychain first on macOS, then the credentials file."""
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    raw = result.stdout.strip()
                    data = json.loads(raw)
                    creds = data.get("claudeAiOauth", {})
                    if isinstance(creds, dict) and creds.get("accessToken"):
                        return creds
            except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, OSError,
                    AttributeError):
                pass

        # Fallback: read from credentials file (Linux, or macOS if Keychain failed)
        try:
            creds_path = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / ".credentials.json"
            data = json.loads(creds_path.read_text())
            creds = data.get("claudeAiOauth", {})
            return creds if isinstance(creds, dict) and creds.get("accessToken") else None
        except (json.JSONDecodeError, KeyError, OSError, AttributeError):
            return None

    @staticmethod
    def fetch_usage_json(token: str, timeout: float = 10.0) -> Tuple[Optional[dict], Optional[str], Optional[float]]:
        """``GET /api/oauth/usage`` as parsed JSON: (data, error, retry-after seconds).

        Never raises. A 429's ``Retry-After`` comes back so the caller can back off.
        """
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            if not isinstance(data, dict):
                return None, "unexpected response", None
            return data, None, None
        except urllib.error.HTTPError as e:
            retry = None
            try:
                retry = float(e.headers.get("Retry-After")) if e.headers else None
            except (TypeError, ValueError):
                retry = None
            return None, f"HTTP {e.code}", retry
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as e:
            return None, str(e), None

    @staticmethod
    def _fetch_usage(token: str) -> UsageSnapshot:
        """Fetch usage data from the Anthropic API."""
        try:
            req = urllib.request.Request(
                USAGE_URL,
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
