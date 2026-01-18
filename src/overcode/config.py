"""
Configuration loading for overcode.

Config file location: ~/.overcode/config.yaml

Example config:
    default_standing_instructions: "Approve file read/write permission requests"
    tmux_session: "agents"
"""

from pathlib import Path
from typing import Optional
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
