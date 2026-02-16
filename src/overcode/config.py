"""
Configuration loading for overcode.

Config file location: ~/.overcode/config.yaml

Example config:
    default_standing_instructions: "Approve file read/write permission requests"
    tmux_session: "agents"
"""

import socket
from pathlib import Path
from typing import List, Optional
import yaml


CONFIG_PATH = Path.home() / ".overcode" / "config.yaml"


def load_config() -> dict:
    """Load configuration from ~/.overcode/config.yaml.

    Returns empty dict if file doesn't exist or is invalid.
    """
    if not CONFIG_PATH.exists():
        return {}

    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
            return config if isinstance(config, dict) else {}
    except (yaml.YAMLError, IOError):
        return {}


def get_default_standing_instructions() -> str:
    """Get default standing instructions from config.

    Returns empty string if not configured.
    """
    config = load_config()
    return config.get("default_standing_instructions", "")


def get_relay_config() -> Optional[dict]:
    """Get relay configuration for pushing state to cloud.

    Returns None if relay is not configured or disabled.

    Config format in ~/.overcode/config.yaml:
        relay:
          enabled: true
          url: https://your-worker.workers.dev/update
          api_key: your-secret-key
          interval: 30  # seconds between pushes (optional, default 30)
    """
    config = load_config()
    relay = config.get("relay", {})

    if not relay.get("enabled", False):
        return None

    url = relay.get("url")
    api_key = relay.get("api_key")

    if not url or not api_key:
        return None

    return {
        "url": url,
        "api_key": api_key,
        "interval": relay.get("interval", 30),
    }


def get_summarizer_config() -> dict:
    """Get summarizer configuration for AI summaries.

    Config file takes precedence, environment variables are fallback.

    Config format in ~/.overcode/config.yaml:
        summarizer:
          api_url: https://api.openai.com/v1/chat/completions
          model: gpt-4o-mini
          api_key_var: OPENAI_API_KEY  # env var name containing the key

    Environment variable fallbacks:
        OVERCODE_SUMMARIZER_API_URL
        OVERCODE_SUMMARIZER_MODEL
        OVERCODE_SUMMARIZER_API_KEY_VAR

    Returns:
        Dict with api_url, model, and api_key (resolved from env var)
    """
    import os

    # Defaults
    default_api_url = "https://api.openai.com/v1/chat/completions"
    default_model = "gpt-4o-mini"
    default_api_key_var = "OPENAI_API_KEY"

    config = load_config()
    summarizer = config.get("summarizer", {})

    # Config file takes precedence, env vars are fallback
    api_url = summarizer.get("api_url") or os.environ.get("OVERCODE_SUMMARIZER_API_URL") or default_api_url
    model = summarizer.get("model") or os.environ.get("OVERCODE_SUMMARIZER_MODEL") or default_model
    api_key_var = summarizer.get("api_key_var") or os.environ.get("OVERCODE_SUMMARIZER_API_KEY_VAR") or default_api_key_var

    # Resolve the actual API key from the configured env var
    api_key = os.environ.get(api_key_var)

    return {
        "api_url": api_url,
        "model": model,
        "api_key": api_key,
        "api_key_var": api_key_var,
    }


def get_timeline_config() -> dict:
    """Get timeline display configuration.

    Config format in ~/.overcode/config.yaml:
        timeline:
          hours: 3.0  # How many hours of history to show

    Returns:
        Dict with timeline settings (hours)
    """
    default_hours = 3.0

    config = load_config()
    timeline = config.get("timeline", {})
    hours = timeline.get("hours", default_hours)

    return {
        "hours": hours,
    }


def get_web_time_presets() -> list:
    """Get time presets for the web analytics dashboard.

    Returns list of preset dictionaries with name, start, end times.
    Falls back to defaults if not configured.

    Config format in ~/.overcode/config.yaml:
        web:
          time_presets:
            - name: "Morning"
              start: "09:00"
              end: "12:00"
            - name: "Full Day"
              start: "09:00"
              end: "17:00"
            - name: "Night Owl"
              start: "22:00"
              end: "02:00"
    """
    config = load_config()
    web_config = config.get("web", {})
    presets = web_config.get("time_presets", None)

    if presets and isinstance(presets, list):
        # Validate and normalize presets
        valid_presets = []
        for p in presets:
            if isinstance(p, dict) and "name" in p:
                valid_presets.append({
                    "name": p.get("name", ""),
                    "start": p.get("start"),
                    "end": p.get("end"),
                })
        if valid_presets:
            # Always add "All Time" at the end
            if not any(p["name"] == "All Time" for p in valid_presets):
                valid_presets.append({"name": "All Time", "start": None, "end": None})
            return valid_presets

    # Default presets
    return [
        {"name": "Morning", "start": "09:00", "end": "12:00"},
        {"name": "Afternoon", "start": "13:00", "end": "17:00"},
        {"name": "Full Day", "start": "09:00", "end": "17:00"},
        {"name": "Evening", "start": "18:00", "end": "22:00"},
        {"name": "All Time", "start": None, "end": None},
    ]


def get_time_context_config() -> dict:
    """Get time context configuration for the time-context hook.

    Config format in ~/.overcode/config.yaml:
        time_context:
          office_start: 9
          office_end: 17
          heartbeat_interval_minutes: 15  # omit to disable

    Returns:
        Dict with office_start (int), office_end (int),
        heartbeat_interval_minutes (Optional[int])
    """
    config = load_config()
    tc = config.get("time_context", {})

    return {
        "office_start": tc.get("office_start", 9),
        "office_end": tc.get("office_end", 17),
        "heartbeat_interval_minutes": tc.get("heartbeat_interval_minutes"),
    }


def get_hostname() -> str:
    """Get configured hostname or system hostname.

    Config format in ~/.overcode/config.yaml:
        hostname: "mac-studio"

    Returns:
        Configured hostname or socket.gethostname()
    """
    config = load_config()
    return config.get("hostname") or socket.gethostname()


def get_web_api_key() -> Optional[str]:
    """Get API key for web server authentication.

    Required when binding to non-localhost addresses.

    Config format in ~/.overcode/config.yaml:
        web:
          api_key: "some-shared-secret"

    Returns:
        API key string or None if not configured
    """
    config = load_config()
    web = config.get("web", {})
    return web.get("api_key") or None


def get_sisters_config() -> List[dict]:
    """Get sister instance configuration for cross-machine monitoring.

    Config format in ~/.overcode/config.yaml:
        sisters:
          - name: "macbook-pro"
            url: "http://localhost:15337"
          - name: "desktop"
            url: "http://localhost:25337"
            api_key: "secret"

    Returns:
        List of dicts with name, url, and optional api_key. Empty list if unconfigured.
    """
    config = load_config()
    sisters = config.get("sisters", [])
    if not isinstance(sisters, list):
        return []

    result = []
    for s in sisters:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        url = s.get("url")
        if not name or not url:
            continue
        entry = {"name": name, "url": url.rstrip("/")}
        api_key = s.get("api_key")
        if api_key:
            entry["api_key"] = api_key
        result.append(entry)

    return result
