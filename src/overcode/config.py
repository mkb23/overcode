"""
Configuration loading for overcode.

Config file location: ~/.overcode/config.yaml ($OVERCODE_DIR/config.yaml;
see paths.config_file).

Example config:
    default_standing_instructions: "Approve file read/write permission requests"
    tmux_session: "agents"
"""

import copy
import os
import re
import socket
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Tuple
import yaml

from . import paths
from .stat_gate import FileSignature, is_settled, stat_signature


# A test points this at its own file; None means paths.config_file().
CONFIG_PATH: Optional[Path] = None


def config_path() -> Path:
    """The config.yaml this process reads and writes."""
    return CONFIG_PATH if CONFIG_PATH is not None else paths.config_file()


# The one parse of config.yaml every reader in the process shares (this
# module's getters, load_config's copies and settings.UserConfig), with the
# (path, file signature) it came from. Each read costs one os.stat; the
# file is parsed again only when its signature moves, so a long-lived
# daemon sees an edit on its next read. No TTL.
_config_lock = threading.Lock()
_config_cache: Optional[dict] = None
_config_cache_key: Optional[Tuple[Path, Optional[FileSignature]]] = None


def _clear_config_cache() -> None:
    """Forget the parsed config; the next read parses the file."""
    global _config_cache, _config_cache_key
    with _config_lock:
        _config_cache = None
        _config_cache_key = None


_TOP_LEVEL_KEY = re.compile(r"^([^\s#][^:]*):")


def _top_level_blocks(lines: List[str]) -> dict:
    """{key: (start, end)} line ranges of each top-level mapping entry.

    A block is the key's line plus the indented (and interior blank) lines
    under it; column-0 comments between blocks belong to no block, so an
    edit never touches them.
    """
    blocks: dict = {}
    i = 0
    while i < len(lines):
        m = _TOP_LEVEL_KEY.match(lines[i])
        if not m:
            i += 1
            continue
        start, i = i, i + 1
        while i < len(lines):
            line = lines[i]
            if line[:1] in (" ", "\t"):
                i += 1
            elif not line.strip():
                nxt = next((ln for ln in lines[i:] if ln.strip()), "")
                if nxt[:1] in (" ", "\t"):
                    i += 1
                else:
                    break
            else:
                break
        blocks[m.group(1).strip().strip("'\"")] = (start, i)
    return blocks


def _merge_into_text(text: str, config: dict) -> str:
    """``text`` (the current config.yaml) with ``config``'s changes applied.

    Only the top-level entries whose values changed are rewritten; every
    other line, comments included, is kept as the person wrote it.
    """
    try:
        current = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError:
        current = None
    if not isinstance(current, dict):
        return yaml.dump(config, default_flow_style=False, sort_keys=False)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    blocks = _top_level_blocks(lines)
    # Edit from the bottom up so earlier line numbers stay valid
    edits = []
    for key in set(current) | set(config):
        if key in config and current.get(key) == config[key] and key in current:
            continue
        rendered = (yaml.dump({key: config[key]}, default_flow_style=False, sort_keys=False)
                    if key in config else "")
        if key in blocks:
            edits.append((blocks[key], rendered))
        elif key in config:
            edits.append(((len(lines), len(lines)), rendered))
    for (start, end), rendered in sorted(edits, key=lambda e: e[0], reverse=True):
        lines[start:end] = [rendered] if rendered else []
    return "".join(lines)


def save_config(config: dict) -> None:
    """Save ``config`` to ~/.overcode/config.yaml, keeping the person's comments.

    Only the top-level entries that changed are rewritten, and the file is
    replaced atomically, so a crash never leaves a half-written config.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old_text = path.read_text()
    except FileNotFoundError:
        old_text = ""
    new_text = _merge_into_text(old_text, config)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # The rename moved the signature already; this only saves the stat
    _clear_config_cache()


def load_config() -> dict:
    """Load configuration from ~/.overcode/config.yaml, as a copy to change and save.

    Returns empty dict if file doesn't exist or is invalid.
    """
    return copy.deepcopy(_cached_config())


def _read_config(path: Path) -> Tuple[Optional[FileSignature], dict]:
    """(signature of the bytes parsed, parsed mapping); {} when missing or invalid."""
    try:
        with open(path) as f:
            sig = FileSignature.of(os.fstat(f.fileno()))
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError:
                data = None
    except OSError:
        return None, {}
    return sig, data if isinstance(data, dict) else {}


def _cached_config() -> dict:
    """The parsed config, shared by every reader: treat it as read-only.

    One os.stat per call; a parse only when the file's signature (or the
    config path) changed. A parse of a file written in the last instant is
    returned but not remembered (stat_gate.is_settled), so two quick
    same-size writes cannot leave a stale parse behind.
    """
    global _config_cache, _config_cache_key
    path = config_path()
    sig = stat_signature(path)
    with _config_lock:
        if _config_cache is not None and _config_cache_key == (path, sig):
            return _config_cache
        read_sig, data = _read_config(path)
        if read_sig is None or is_settled(read_sig):
            _config_cache, _config_cache_key = data, (path, read_sig)
        else:
            _config_cache, _config_cache_key = None, None
        return data


def _get_config_value(key_path: str, default=None):
    """Load config and traverse a dot-separated key path.

    Example: _get_config_value("web.api_key") loads config["web"]["api_key"].
    Returns default if any key is missing or intermediate value isn't a dict.
    """
    config = _cached_config()
    keys = key_path.split(".")
    value = config
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
        if value is None:
            return default
    return value


def get_default_standing_instructions() -> str:
    """Get default standing instructions from config.

    Returns empty string if not configured.
    """
    return _get_config_value("default_standing_instructions", "")


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
    default_api_type = "openai"

    # Config file takes precedence, env vars are fallback
    api_type = _get_config_value("summarizer.api_type") or os.environ.get("OVERCODE_SUMMARIZER_API_TYPE") or default_api_type
    api_url = _get_config_value("summarizer.api_url") or os.environ.get("OVERCODE_SUMMARIZER_API_URL") or default_api_url
    model = _get_config_value("summarizer.model") or os.environ.get("OVERCODE_SUMMARIZER_MODEL") or default_model
    api_key_var = _get_config_value("summarizer.api_key_var") or os.environ.get("OVERCODE_SUMMARIZER_API_KEY_VAR") or default_api_key_var

    # Resolve the actual API key from the configured env var
    api_key = os.environ.get(api_key_var)

    # Cost cap: default $100, configurable
    cost_cap = _get_config_value("summarizer.cost_cap")
    if cost_cap is None:
        cost_cap = 100.0
    else:
        cost_cap = float(cost_cap)

    return {
        "api_type": api_type,
        "api_url": api_url,
        "model": model,
        "api_key": api_key,
        "api_key_var": api_key_var,
        "cost_cap": cost_cap,
    }


def get_timeline_config() -> dict:
    """Get timeline display configuration.

    Config format in ~/.overcode/config.yaml:
        timeline:
          hours: 3.0  # How many hours of history to show

    Returns:
        Dict with timeline settings (hours)
    """
    return {
        "hours": _get_config_value("timeline.hours", 3.0),
    }


def get_enhanced_context_config() -> dict:
    """Get enhanced context configuration for the enhanced-context hook.

    Config format in ~/.overcode/config.yaml:
        enhanced_context:
          office_start: 9
          office_end: 17
          heartbeat_interval_minutes: 15  # omit to disable

    Falls back to legacy 'time_context' key for backwards compatibility.

    Returns:
        Dict with office_start (int), office_end (int),
        heartbeat_interval_minutes (Optional[int])
    """
    # Try new key first, fall back to legacy key
    office_start = _get_config_value("enhanced_context.office_start")
    if office_start is None:
        office_start = _get_config_value("time_context.office_start", 9)
    office_end = _get_config_value("enhanced_context.office_end")
    if office_end is None:
        office_end = _get_config_value("time_context.office_end", 17)
    hb_interval = _get_config_value("enhanced_context.heartbeat_interval_minutes")
    if hb_interval is None:
        hb_interval = _get_config_value("time_context.heartbeat_interval_minutes")
    return {
        "office_start": office_start,
        "office_end": office_end,
        "heartbeat_interval_minutes": hb_interval,
    }


def get_hostname() -> str:
    """Get configured hostname or system hostname.

    Config format in ~/.overcode/config.yaml:
        hostname: "mac-studio"

    Returns:
        Configured hostname or socket.gethostname()
    """
    return _get_config_value("hostname") or socket.gethostname()


def get_web_port() -> int:
    """Get configured API server port.

    Config format in ~/.overcode/config.yaml:
        web:
          port: 8081

    Returns:
        Configured port or 8080 (default)
    """
    return int(_get_config_value("web.port", 8080))


def get_web_host() -> str:
    """Get configured API server bind host.

    Config format in ~/.overcode/config.yaml:
        web:
          host: "0.0.0.0"

    Returns:
        Configured host or "127.0.0.1" (default)
    """
    return _get_config_value("web.host", "127.0.0.1")


def get_web_api_key() -> Optional[str]:
    """Get API key for API server authentication.

    Required when binding to non-localhost addresses, and for
    web.allow_control (control routes answer 403 without it).

    Config format in ~/.overcode/config.yaml:
        web:
          api_key: "some-shared-secret"

    Returns:
        API key string or None if not configured
    """
    return _get_config_value("web.api_key") or None


def get_web_allow_control() -> bool:
    """Check if remote control is enabled for the API server.

    When False (default), all POST/PUT/DELETE endpoints return 403. They
    also return 403 when this is True but web.api_key is not set.

    Config format in ~/.overcode/config.yaml:
        web:
          allow_control: true

    Returns:
        True if remote control is enabled, False otherwise
    """
    return bool(_get_config_value("web.allow_control", False))


def get_tmux_toggle_key() -> str | None:
    """Get configured tmux pane toggle key.

    Config format in ~/.overcode/config.yaml:
        tmux:
          toggle_key: "Tab"  # or "C-]", "BTab", etc.

    Returns:
        Configured key name or None if not set.
    """
    return _get_config_value("tmux.toggle_key")


def set_tmux_toggle_key(key: str) -> None:
    """Save the tmux pane toggle key to config.

    Args:
        key: tmux key name (e.g. "Tab", "C-]").
    """
    config = load_config()
    tmux_section = config.get("tmux", {})
    if not isinstance(tmux_section, dict):
        tmux_section = {}
    tmux_section["toggle_key"] = key
    config["tmux"] = tmux_section
    save_config(config)


# Default passthru keys — overcode hotkeys forwarded straight to the focused
# agent's tmux pane rather than being handled locally. Keys are the overcode
# hotkey names; values are the tmux key strings actually sent. A user can
# disable a slot by removing it from config.yaml, or remap it to send a
# different key (e.g. forward ctrl+o as "escape"). Adding fully new keys
# (beyond this default set) requires editing config.yaml — see the commented
# examples section below.
DEFAULT_PASSTHRU_KEYS: dict[str, str] = {
    "enter": "enter",
    "escape": "escape",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "ctrl+o": "ctrl+o",
}


def get_passthru_keys() -> dict[str, str]:
    """Return the active passthru-key map.

    Merges defaults with user config. A user can:
      - disable a default slot by setting its value to null/empty in config
      - remap a slot to a different target (e.g. ctrl+o -> escape)
      - add new slots if their key name exists as a class-level BINDING

    Config format in ~/.overcode/config.yaml:
        passthru_keys:
          enter: "enter"
          escape: "escape"
          "1": "1"
          # disable the 5 key:
          "5": null
          # remap ctrl+o to send escape instead:
          ctrl+o: "escape"
    """
    merged = dict(DEFAULT_PASSTHRU_KEYS)
    user = _get_config_value("passthru_keys", {})
    if isinstance(user, dict):
        for k, v in user.items():
            if v in (None, ""):
                merged.pop(k, None)
            elif isinstance(v, str):
                merged[k] = v
    return merged


def save_passthru_keys(mapping: dict[str, str]) -> None:
    """Save the passthru-key overlay to config.yaml.

    Writes only the deltas from DEFAULT_PASSTHRU_KEYS so the config file
    stays small. A slot present in defaults but missing from ``mapping``
    is written as ``null`` to record the user's intent to disable it.
    """
    deltas: dict[str, Optional[str]] = {}
    for k, default_v in DEFAULT_PASSTHRU_KEYS.items():
        new_v = mapping.get(k)
        if new_v is None:
            deltas[k] = None
        elif new_v != default_v:
            deltas[k] = new_v
    # Also persist any user-added slots beyond the defaults
    for k, v in mapping.items():
        if k not in DEFAULT_PASSTHRU_KEYS and v:
            deltas[k] = v

    config = load_config()
    if deltas:
        config["passthru_keys"] = deltas
    else:
        config.pop("passthru_keys", None)
    save_config(config)


def get_new_agent_defaults() -> dict:
    """Get new-agent default settings from config.

    Config format in ~/.overcode/config.yaml:
        new_agent_defaults:
          bypass_permissions: false
          agent_teams: false
          backend: claude-code

    Returns:
        Dict with bypass_permissions (bool), agent_teams (bool), provider,
        wrapper, backend (agent CLI name, always resolved — never empty),
        and backend_explicit (bool: True when config.yaml actually names a
        backend, False when ``backend`` above is just the built-in default
        filled in for a config with no opinion — the G modal in the TUI uses
        this to distinguish "(unset)" from an explicit "claude-code").
    """
    from .backends import DEFAULT_BACKEND

    defaults = _get_config_value("new_agent_defaults", {})
    return {
        "bypass_permissions": bool(defaults.get("bypass_permissions", False)),
        "agent_teams": bool(defaults.get("agent_teams", False)),
        "provider": defaults.get("provider", "web"),
        "wrapper": defaults.get("wrapper", ""),
        "backend": defaults.get("backend") or DEFAULT_BACKEND,
        "backend_explicit": bool(defaults.get("backend")),
        "skill_profile": defaults.get("skill_profile") or None,
    }


def save_new_agent_defaults(defaults: dict) -> None:
    """Save new-agent default settings to config.

    Args:
        defaults: Dict with bypass_permissions and agent_teams booleans.
    """
    config = load_config()
    config["new_agent_defaults"] = defaults
    save_config(config)


def get_backend_telemetry_enabled(backend_name: str) -> bool:
    """Whether a backend should install its telemetry footprint at launch.

    Config format in ~/.overcode/config.yaml:
        backend_telemetry:
          opencode: off   # skip installing .opencode/plugins/overcode-telemetry.js
          opencode2: off  # skip installing .opencode/plugins/overcode-telemetry-v2/ (bundled plugin)
          codex: on       # (default) inject -c hooks.*=... overrides
          grok: off       # skip installing ~/.grok/hooks/overcode.json

    Default is "on" for every backend, so existing installs are unaffected
    until a user opts a backend out. ``claude-code`` is exempt and always
    returns True regardless of config: its hooks ride per-launch
    ``--settings`` flags and leave no on-disk footprint, so there is nothing
    to opt out of.

    Returns:
        True unless config.yaml explicitly turns this backend's telemetry
        off (accepts "off"/"false"/"0"/"no", case-insensitively, or a
        literal boolean False).
    """
    if backend_name == "claude-code":
        return True
    value = _get_config_value(f"backend_telemetry.{backend_name}")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("off", "false", "0", "no")
    return True


def get_sync_branch() -> str:
    """Get the branch name used by the `cc` sync-to-main chord.

    Config format in ~/.overcode/config.yaml:
        sync:
          branch: master   # or main, or any branch name

    Returns:
        Branch name to check out during sync (default: "main").
    """
    value = _get_config_value("sync.branch", "main")
    return str(value).strip() or "main"


def get_bedrock_config() -> dict:
    """Get AWS Bedrock configuration.

    Config format in ~/.overcode/config.yaml:
        bedrock:
          region: us-east-1

    Returns:
        Dict with region (str).
    """
    bedrock = _get_config_value("bedrock", {})
    return {
        "region": bedrock.get("region", "us-east-1"),
    }


def get_jobs_retention_hours() -> float:
    """Get job retention period in hours.

    Config format in ~/.overcode/config.yaml:
        jobs:
          retention_hours: 24

    Returns:
        Retention hours (default 24)
    """
    return float(_get_config_value("jobs.retention_hours", 24))


def get_history_retention_config() -> dict:
    """Get rotation/retention settings for status-history and diagnostic CSVs (#465, #468).

    agent_status_history.csv is archival — it's rotated to a compressed
    .csv.gz alongside the active file and retained for status_history_max_days.
    diagnostics/event_loop_timing.csv is diagnostic-only — it's hard-capped
    at event_loop_timing_cap_mb (oldest rows dropped, not archived) and can
    be disabled outright via event_loop_timing_enabled.

    Config format in ~/.overcode/config.yaml:
        history_retention:
          status_history_max_days: 540    # delete rotated archives older than this (~18 months)
          status_history_rotate_mb: 50    # rotate the active CSV past this size
          event_loop_timing_cap_mb: 100   # hard cap for diagnostics/event_loop_timing.csv
          event_loop_timing_enabled: true # false disables the heartbeat probe entirely

    Returns:
        Dict with the four keys above; missing/invalid entries fall back to
        the defaults shown.
    """
    cfg = _get_config_value("history_retention", {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "status_history_max_days": float(cfg.get("status_history_max_days", 540)),
        "status_history_rotate_mb": float(cfg.get("status_history_rotate_mb", 50)),
        "event_loop_timing_cap_mb": float(cfg.get("event_loop_timing_cap_mb", 100)),
        "event_loop_timing_enabled": bool(cfg.get("event_loop_timing_enabled", True)),
    }


def get_model_metadata_config() -> dict:
    """Settings for the models.dev catalog behind CTX%/pricing lookups (#473).

    Off by default: a monitoring daemon should not make unattended outbound
    requests. ``overcode models refresh`` is the on-demand path; this knob
    lets the daemon do the same fetch itself once the local cache is older
    than ``max_age_days``.

    Config format in ~/.overcode/config.yaml:
        model_metadata:
          auto_refresh: false   # daemon refreshes ~/.overcode/cache/model_metadata.json from models.dev
          max_age_days: 7       # ...once the cache is older than this
    """
    cfg = _get_config_value("model_metadata", {})
    if not isinstance(cfg, dict):
        cfg = {}
    try:
        max_age = float(cfg.get("max_age_days", 7))
    except (TypeError, ValueError):
        max_age = 7.0
    return {
        "auto_refresh": bool(cfg.get("auto_refresh", False)),
        "max_age_days": max(0.0, max_age),
    }


def get_session_archive_config() -> dict:
    """How long terminated sessions stay in sessions.json before being archived.

    sessions.json is the shared, whole-file-rewritten state every TUI and
    daemon parses; the monitor daemon moves entries whose status has been
    ``terminated`` for longer than the grace to the append-only
    ``archive.jsonl`` (the same record ``overcode cleanup`` writes), so the
    live file stays the size of the live fleet. The TUI's "show terminated"
    ghosts keep showing a killed agent for the whole grace.

    Config format in ~/.overcode/config.yaml:
        session_archive:
          terminated_grace_seconds: 3600   # -1 (or any negative) disables automatic archiving

    Returns:
        Dict with ``terminated_grace_seconds`` (float); a missing or invalid
        entry falls back to ``settings.DAEMON.terminated_archive_grace_seconds``.
    """
    from .settings import DAEMON

    cfg = _get_config_value("session_archive", {})
    if not isinstance(cfg, dict):
        cfg = {}
    default = float(DAEMON.terminated_archive_grace_seconds)
    try:
        grace = float(cfg.get("terminated_grace_seconds", default))
    except (TypeError, ValueError):
        grace = default
    return {"terminated_grace_seconds": grace}


def get_monitor_daemon_config() -> dict:
    """The monitor daemon's loop interval while nobody is watching.

    With no client attached to the agents tmux session, no fresh TUI
    keypress heartbeat and no TUI touching its attended file, the daemon
    stretches its loop from ``interval_fast`` (2 s) to this many seconds.
    Status history is written on change, so the timeline it keeps for the
    user's return has no holes at this resolution. Read once when the
    daemon starts.

    Config format in ~/.overcode/config.yaml:
        monitor_daemon:
          interval_unattended_seconds: 10   # >= interval_fast
          # (the default is settings.DAEMON.interval_unattended)

    Returns:
        Dict with ``interval_unattended`` (int); a missing or invalid entry,
        or one below ``interval_fast``, falls back to the default.
    """
    from .settings import DAEMON

    cfg = _get_config_value("monitor_daemon", {})
    if not isinstance(cfg, dict):
        cfg = {}
    default = int(DAEMON.interval_unattended)
    try:
        interval = int(cfg.get("interval_unattended_seconds", default))
    except (TypeError, ValueError):
        interval = default
    if interval < DAEMON.interval_fast:
        interval = default
    return {"interval_unattended": interval}


def get_sisters_config() -> List[dict]:
    """Get sister instance configuration for cross-machine monitoring.

    Config format in ~/.overcode/config.yaml:
        sisters:
          - name: "macbook-pro"
            url: "http://localhost:15337"
          - name: "desktop"
            url: "http://localhost:25337"
            api_key: "secret"
            ssh: "user@desktop"        # optional: enables tmux attach + auto-provisioning
            tmux_session: "agents"     # optional: remote tmux session name (default: "agents")

    Returns:
        List of dicts with name, url, and optional api_key/ssh/tmux_session. Empty list if unconfigured.
    """
    sisters = _get_config_value("sisters", [])
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
        # SSH connectivity (optional)
        ssh = s.get("ssh")
        if ssh:
            entry["ssh"] = ssh
        tmux_session = s.get("tmux_session")
        if tmux_session:
            entry["tmux_session"] = tmux_session
        result.append(entry)

    return result


def get_sister_by_name(name: str) -> Optional[dict]:
    """Look up a sister config by name.

    Returns:
        Sister config dict (name, url, optional api_key) or None if not found.
    """
    for sister in get_sisters_config():
        if sister["name"] == name:
            return sister
    return None
