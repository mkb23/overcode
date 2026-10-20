"""
Configuration loading for overcode.

Config file location: ~/.overcode/config.yaml ($OVERCODE_DIR/config.yaml;
see paths.config_file).

Example config:
    default_standing_instructions: "Approve file read/write permission requests"
    tmux_session: "agents"
"""

import copy
import logging
import os
import re
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple
import yaml

from . import paths
from .stat_gate import FileSignature, is_settled, stat_signature

logger = logging.getLogger(__name__)


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


def _is_list_item(line: str) -> bool:
    """A column-0 ``- item`` line: YAML (and PyYAML's default dump) allows a
    key's list items unindented, and they belong to that key's block."""
    return line[:1] == "-" and line[1:2] in (" ", "\t", "\n", "\r", "")


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
            if line[:1] in (" ", "\t") or _is_list_item(line):
                i += 1
            elif not line.strip():
                nxt = next((ln for ln in lines[i:] if ln.strip()), "")
                if nxt[:1] in (" ", "\t") or _is_list_item(nxt):
                    i += 1
                else:
                    break
            else:
                break
        blocks[m.group(1).strip().strip("'\"")] = (start, i)
    return blocks


def _parse_mapping(text: str) -> Optional[dict]:
    """``text`` parsed as a config mapping; None when it is not one.

    An empty or comments-only file is an empty mapping. A YAML error, or a
    top-level list or scalar, is None: the file holds something the person
    wrote that this module cannot read back, so it must not be rewritten.
    """
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError:
        return None
    if data is None:
        return {}
    return data if isinstance(data, dict) else None


def _merge_into_text(text: str, config: dict) -> str:
    """``text`` (the current config.yaml) with ``config``'s changes applied.

    Only the top-level entries whose values changed are rewritten; every
    other line, comments included, is kept as the person wrote it.
    ``text`` must parse as a mapping (save_config sets anything else aside).
    """
    current = _parse_mapping(text)
    if current is None:
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
    merged = "".join(lines)
    # Never write a file that doesn't read back as ``config``: a layout the
    # block finder misreads costs the comments, not the config.
    try:
        if yaml.safe_load(merged) == config:
            return merged
    except yaml.YAMLError:
        pass
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _set_aside_unreadable(path: Path) -> Path:
    """Rename ``path`` to ``<name>.broken-<timestamp>`` next to it and return
    the new path, so a save never overwrites a config it cannot parse."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = path.with_name(f"{path.name}.broken-{stamp}")
    n = 1
    while aside.exists():
        aside = path.with_name(f"{path.name}.broken-{stamp}-{n}")
        n += 1
    os.replace(path, aside)
    return aside


def save_config(config: dict) -> None:
    """Save ``config`` to ~/.overcode/config.yaml, keeping the person's comments.

    Only the top-level entries that changed are rewritten, and the file is
    replaced atomically, so a crash never leaves a half-written config.

    A symlinked config.yaml is written through to its target, keeping the
    link and the target's mode. A config.yaml that does not parse (load_config
    read it as {}) is never overwritten: it is moved aside to
    ``config.yaml.broken-<timestamp>`` with a warning, and the save then
    writes a fresh file, so the person's text survives to be fixed by hand.
    """
    # Replace the file a symlinked config points at, not the link itself
    path = Path(os.path.realpath(config_path()))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old_text = path.read_text()
        old_mode: Optional[int] = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        old_text, old_mode = "", None
    if _parse_mapping(old_text) is None:
        aside = _set_aside_unreadable(path)
        logger.warning(
            "%s could not be parsed as a YAML mapping; moved it to %s and "
            "saved a new config. Fix the old file and copy your settings back.",
            path, aside,
        )
        old_text = ""
    new_text = _merge_into_text(old_text, config)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        if old_mode is not None:
            os.chmod(tmp, old_mode)
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
          routing: global      # optional: the inference-profile prefix in use

    Returns:
        Dict with region (str) and routing (str or None).
    """
    bedrock = _get_config_value("bedrock", {})
    if not isinstance(bedrock, dict):
        bedrock = {}
    routing = bedrock.get("routing")
    return {
        "region": bedrock.get("region", "us-east-1"),
        "routing": routing if isinstance(routing, str) and routing else None,
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


FINISHED_ROW_SECONDS_DEFAULT = 30.0


def get_observability_config() -> dict:
    """How the TUI shows the work an agent delegates inside its session.

    docs/design/agent-observability-0.6.1.md, decision 3: a subagent,
    workflow agent or teammate that has finished keeps its own (dimmed) row
    under its parent for ``finished_row_seconds``, then folds into the
    parent's 👥 counts. The counts always include it; the rows arrive with
    expandable groups (phase 2).

    Phase 3: a Claude background session (``claude --bg``, ``/bg``) under
    a fleet agent's directory gets a 🌙 row of its own (decision 1); a
    finished one keeps it for the same ``finished_row_seconds``.

    Config format in ~/.overcode/config.yaml:
        observability:
          finished_row_seconds: 30   # 0 folds a finished agent at once
          background_sessions: true  # false: no 🌙 rows

    Returns:
        Dict with ``finished_row_seconds`` (float, never negative; a
        missing or invalid entry falls back to 30),
        and ``background_sessions`` (bool, default True). An old
        ``done_rows`` entry (0.6.1 betas' done line) is ignored.
    """
    cfg = _get_config_value("observability", {})
    if not isinstance(cfg, dict):
        cfg = {}
    background = cfg.get("background_sessions", True)
    return {
        "finished_row_seconds": _non_negative(
            cfg.get("finished_row_seconds", FINISHED_ROW_SECONDS_DEFAULT),
            FINISHED_ROW_SECONDS_DEFAULT),
        "background_sessions": background if isinstance(background, bool) else True,
    }


def save_finished_row_seconds(seconds: float) -> None:
    """Set ``observability.finished_row_seconds`` in config.yaml (the
    visibility dialog); the engine reads it on its next publish."""
    config = load_config()
    section = config.get("observability")
    if not isinstance(section, dict):
        section = {}
    section["finished_row_seconds"] = float(seconds)
    config["observability"] = section
    save_config(config)


_idle_warned: set = set()


def get_idle_config() -> dict:
    """The idle stages (idle_stages.py): seconds, None for never.

    Config format in ~/.overcode/config.yaml:
        idle:
          drowsy_after: 1h    # 📥 turns 😴 (never / off to keep 📥)
          sleep_after: never  # the engine puts it to sleep (30m, 2h, 3d...)

    An unreadable value is the default, with a warning in the log.
    """
    from .idle_stages import DROWSY_AFTER_DEFAULT, SLEEP_AFTER_DEFAULT, parse_after
    section = _get_config_value("idle", {})
    if not isinstance(section, dict):
        section = {}
    out = {}
    for key, default in (("drowsy_after", DROWSY_AFTER_DEFAULT),
                         ("sleep_after", SLEEP_AFTER_DEFAULT)):
        raw = section.get(key)
        value = parse_after(raw, default)
        warning = f"idle.{key}: not a duration: {raw!r}"
        if parse_after(raw, -1.0) == -1.0 and warning not in _idle_warned:
            _idle_warned.add(warning)
            logging.getLogger(__name__).warning(warning)
        out[key] = value
    return out


def save_idle_setting(key: str, seconds: Optional[float]) -> None:
    """Set ``idle.<key>`` in config.yaml (the visibility dialog)."""
    from .idle_stages import format_after
    config = load_config()
    section = config.get("idle")
    if not isinstance(section, dict):
        section = {}
    section[key] = format_after(seconds)
    config["idle"] = section
    save_config(config)


_SIZE_UNITS = {"": 1, "B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def parse_size(value) -> int:
    """Bytes from ``1073741824``, ``"1GB"``, ``"500 MB"`` or ``"64kb"``; 0 if unreadable."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if not isinstance(value, str):
        return 0
    text = value.strip().upper().replace(" ", "")
    number = text.rstrip("KMGTB")
    unit = text[len(number):]
    try:
        return max(0, int(float(number) * _SIZE_UNITS[unit]))
    except (KeyError, ValueError):
        return 0


def _non_negative(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def get_agent_history_config() -> dict:
    """How long archived agents' own files (the day bundles) are kept.

    When an agent is archived its directory (hook state, event log, report,
    episode log) is packed into one gzip member of its tmux session's day
    bundle, ``sessions/<tmux>/bundles/<day>.agents.gz``, and removed. Bundles
    are kept for ever unless a limit is set.

    Config format in ~/.overcode/config.yaml:
        agent_history:
          retention_days: 0       # 0 (default) keeps every day; N deletes days older than N
          max_bytes: 0            # 0 (default) no cap; e.g. 1GB drops the oldest days past it
          keep_hook_events: true  # false packs only state, report and episodes

    Returns:
        Dict with ``retention_days`` (float, 0 = for ever), ``max_bytes``
        (int, 0 = no cap) and ``keep_hook_events`` (bool).
    """
    cfg = _get_config_value("agent_history", {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "retention_days": _non_negative(cfg.get("retention_days", 0), 0.0),
        "max_bytes": parse_size(cfg.get("max_bytes", 0)),
        "keep_hook_events": bool(cfg.get("keep_hook_events", True)),
    }


def get_archive_config() -> dict:
    """How long the session archive's months are kept.

    Config format in ~/.overcode/config.yaml:
        archive:
          retention_months: 0     # 0 (default) keeps every month; N deletes older months

    Returns:
        Dict with ``retention_months`` (int, 0 = for ever).
    """
    cfg = _get_config_value("archive", {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {"retention_months": int(_non_negative(cfg.get("retention_months", 0), 0))}


def get_ledger_config() -> dict:
    """The spend ledger: how often a long-running agent's spend is checkpointed.

    The ledger itself is kept for ever (there is no retention knob).

    Config format in ~/.overcode/config.yaml:
        ledger:
          checkpoint_minutes: 60  # a row for an agent still running, at most this often

    Returns:
        Dict with ``checkpoint_minutes`` (float, > 0).
    """
    cfg = _get_config_value("ledger", {})
    if not isinstance(cfg, dict):
        cfg = {}
    minutes = _non_negative(cfg.get("checkpoint_minutes", 60), 60.0)
    return {"checkpoint_minutes": minutes if minutes > 0 else 60.0}


def get_billing_config() -> dict:
    """The billing-code shortlist the TUI cycles through with space.

    Config format in ~/.overcode/config.yaml:
        billing:
          codes: [client-a, client-b, internal]

    Empty by default (the TUI then cycles only the agent's own name). A
    number is taken as its text (``- 1234``). Anything else that is not a
    usable code (a list, a mapping, an empty string), a ``billing`` or
    ``codes`` of the wrong shape and an unknown key under ``billing`` are
    ignored with a warning; this never raises.

    Returns:
        Dict with ``codes`` (list of distinct, stripped strings, in config
        order) and ``warnings`` (list of strings, empty when all is well).
    """
    warnings: List[str] = []
    cfg = _get_config_value("billing", {})
    if not isinstance(cfg, dict):
        return {"codes": [], "warnings": ["billing: should be a mapping with a 'codes' list — ignored"]}
    for key in cfg:
        if key != "codes":
            warnings.append(f"billing: unknown key '{key}' — ignored")
    raw = cfg.get("codes")
    if raw is None:
        raw = []
    elif not isinstance(raw, list):
        warnings.append("billing.codes: should be a list of codes — ignored")
        raw = []
    codes: List[str] = []
    for item in raw:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            item = str(item)
        if not isinstance(item, str) or not item.strip():
            warnings.append(f"billing.codes: {item!r} is not a billing code — ignored")
            continue
        code = item.strip()
        if code not in codes:
            codes.append(code)
    return {"codes": codes, "warnings": warnings}


NAMING_MAX_LENGTH_DEFAULT = 18
_NAMING_KEYS = ("enabled", "advice", "max_length", "rules", "first_after", "max_renames",
                "cost_cap_per_day", "provider", "cli_title")
_NAMING_RULE_KEYS = ("pattern", "max_length", "forbid", "require", "no_repo_prefix",
                     "recent_archived_hours", "llm_check", "warn_humans")
_NAMING_FIRST_AFTER = {"turns": 2, "green_minutes": 3.0, "output_tokens": 15000}


def _positive_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _regex_or_none(value, where: str, warnings: List[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        warnings.append(f"{where}: should be a regex string — ignored")
        return None
    try:
        re.compile(value)
    except re.error as e:
        warnings.append(f"{where}: invalid regex ({e}) — ignored")
        return None
    return value


def get_naming_config() -> dict:
    """Auto-naming and the naming policy (#542, docs/design/auto-naming.md §2a, §6).

    Config format in ~/.overcode/config.yaml:
        naming:
          enabled: false        # the daemon's namer; the policy check runs regardless
          advice: |             # free text: the primary policy, read by the namer
            kebab-case, 1-3 words, what the work is about
          max_length: 18        # the namer's target length
          rules:                # optional deterministic checks on names parents give
            pattern: null       # a regex the name must match
            max_length: null    # a hard limit (none by default)
            forbid: []          # words the name may not contain
            require: null       # a regex the name must contain
            no_repo_prefix: false
            recent_archived_hours: 0   # refuse names archived this recently (0 = off)
            llm_check: false    # ask the summarizer model whether the name follows the advice
            warn_humans: false  # warn (never refuse) a human's -n too
          first_after: {turns: 2, green_minutes: 3, output_tokens: 15000}
          max_renames: 2
          cost_cap_per_day: 0.50
          provider: {...}       # api_type/api_url/model/api_key_var; default: the summarizer: block
          cli_title: true       # keep Claude's own session name (--name, /rename) in step

    Every rule is off unless set: only overcode's name pattern and a name
    already taken are always refused. Unknown keys and bad values are
    ignored with a warning; this never raises.

    Returns:
        Dict with the keys above (``rules`` and ``first_after`` as dicts,
        ``forbid`` a list of lower-cased words) and ``warnings`` (list).
    """
    warnings: List[str] = []
    cfg = _get_config_value("naming", {})
    if not isinstance(cfg, dict):
        warnings.append("naming: should be a mapping — ignored")
        cfg = {}
    for key in cfg:
        if key not in _NAMING_KEYS:
            warnings.append(f"naming: unknown key '{key}' — ignored")

    advice = cfg.get("advice")
    if advice is not None and not isinstance(advice, str):
        warnings.append("naming.advice: should be text — ignored")
        advice = None
    max_length = _positive_int(cfg.get("max_length", NAMING_MAX_LENGTH_DEFAULT))
    if max_length is None:
        warnings.append("naming.max_length: should be a positive number — using "
                        f"{NAMING_MAX_LENGTH_DEFAULT}")
        max_length = NAMING_MAX_LENGTH_DEFAULT

    raw_rules = cfg.get("rules") or {}
    if not isinstance(raw_rules, dict):
        warnings.append("naming.rules: should be a mapping — ignored")
        raw_rules = {}
    for key in raw_rules:
        if key not in _NAMING_RULE_KEYS:
            warnings.append(f"naming.rules: unknown key '{key}' — ignored")
    forbid_raw = raw_rules.get("forbid") or []
    if isinstance(forbid_raw, str):
        forbid_raw = [forbid_raw]
    if not isinstance(forbid_raw, list):
        warnings.append("naming.rules.forbid: should be a list of words — ignored")
        forbid_raw = []
    forbid: List[str] = []
    for word in forbid_raw:
        if isinstance(word, (int, float)) and not isinstance(word, bool):
            word = str(word)
        if not isinstance(word, str) or not word.strip():
            warnings.append(f"naming.rules.forbid: {word!r} is not a word — ignored")
            continue
        if word.strip().lower() not in forbid:
            forbid.append(word.strip().lower())
    rule_max = raw_rules.get("max_length")
    if rule_max is not None:
        rule_max = _positive_int(rule_max)
        if rule_max is None:
            warnings.append("naming.rules.max_length: should be a positive number — ignored")
    rules = {
        "pattern": _regex_or_none(raw_rules.get("pattern"), "naming.rules.pattern", warnings),
        "max_length": rule_max,
        "forbid": forbid,
        "require": _regex_or_none(raw_rules.get("require"), "naming.rules.require", warnings),
        "no_repo_prefix": raw_rules.get("no_repo_prefix") is True,
        "recent_archived_hours": _non_negative(raw_rules.get("recent_archived_hours", 0), 0.0),
        "llm_check": raw_rules.get("llm_check") is True,
        "warn_humans": raw_rules.get("warn_humans") is True,
    }

    raw_first = cfg.get("first_after") or {}
    if not isinstance(raw_first, dict):
        warnings.append("naming.first_after: should be a mapping — ignored")
        raw_first = {}
    first_after = {k: _non_negative(raw_first.get(k, d), d) for k, d in _NAMING_FIRST_AFTER.items()}
    max_renames = cfg.get("max_renames", 2)
    max_renames = max_renames if isinstance(max_renames, int) and not isinstance(
        max_renames, bool) and max_renames >= 0 else 2
    provider = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else None
    if cfg.get("provider") is not None and provider is None:
        warnings.append("naming.provider: should be a mapping — using the summarizer: block")

    return {
        "enabled": cfg.get("enabled") is True,
        "advice": (advice or "").strip(),
        "max_length": max_length,
        "rules": rules,
        "first_after": first_after,
        "max_renames": max_renames,
        "cost_cap_per_day": _non_negative(cfg.get("cost_cap_per_day", 0.50), 0.50),
        "provider": provider,
        "cli_title": cfg.get("cli_title") is not False,
        "warnings": warnings,
    }


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


QUOTA_PLANS = ("pro", "max5x", "max20x", "max", "team", "team_premium", "enterprise")
_QUOTA_KEYS = ("enabled", "calibration", "plan", "weekly_usd", "weeks", "poll_minutes", "breaks")


def get_quota_config() -> dict:
    """The subscription quota model (cost model Phase B).

    Config format in ~/.overcode/config.yaml:
        quota:
          enabled: true        # poll subscription usage in the engine; the Q% column
          calibration: false   # opt in: statusline samples from agents overcode launches
          plan: max20x         # pro | max5x | max20x | team | team_premium | enterprise
                               # (detected from Claude Code's login when unset)
          weekly_usd: 1550     # the prior's API-equivalent $ per week (else the plan's)
          weeks: 4             # history the calibration fits (2-8)
          poll_minutes: 5      # usage API poll (at least 5)
          breaks: [2026-05-06] # known limit changes: a window never spans one

    Unknown keys and values of the wrong shape are ignored with a warning;
    this never raises.

    Returns:
        Dict with ``enabled``, ``calibration`` (bools), ``plan`` (str or
        None), ``weekly_usd`` (float or None), ``weeks`` (int),
        ``poll_minutes`` (float), ``breaks`` (epoch seconds) and
        ``warnings``.
    """
    warnings: List[str] = []
    cfg = _get_config_value("quota", {})
    if not isinstance(cfg, dict):
        warnings.append("quota: should be a mapping — ignored")
        cfg = {}
    for key in cfg:
        if key not in _QUOTA_KEYS:
            warnings.append(f"quota: unknown key '{key}' — ignored")
    plan = cfg.get("plan")
    if plan is not None and plan not in QUOTA_PLANS:
        warnings.append(f"quota.plan: {plan!r} is not one of {', '.join(QUOTA_PLANS)} — ignored")
        plan = None
    usd = cfg.get("weekly_usd")
    if usd is not None and (isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd <= 0):
        warnings.append("quota.weekly_usd: should be a positive number — ignored")
        usd = None
    try:
        weeks = int(cfg.get("weeks", 4))
    except (TypeError, ValueError):
        weeks = 4
    poll = _non_negative(cfg.get("poll_minutes", 5), 5.0)
    breaks, bad = _quota_breaks(cfg.get("breaks"))
    warnings.extend(bad)
    return {
        "enabled": cfg.get("enabled", True) is not False,
        "calibration": cfg.get("calibration", False) is True,
        "plan": plan,
        "weekly_usd": float(usd) if usd is not None else None,
        "weeks": min(max(weeks, 2), 8),
        "poll_minutes": max(5.0, poll),
        "breaks": breaks,
        "warnings": warnings,
    }


def _quota_breaks(raw) -> Tuple[List[float], List[str]]:
    """``quota.breaks`` as epoch seconds (local midnight of each date), and warnings."""
    from datetime import date, datetime

    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["quota.breaks: should be a list of dates — ignored"]
    out: List[float] = []
    warnings: List[str] = []
    for item in raw:
        try:
            if isinstance(item, datetime):
                out.append(item.timestamp())
            elif isinstance(item, date):
                out.append(datetime(item.year, item.month, item.day).timestamp())
            else:
                out.append(datetime.fromisoformat(str(item)).timestamp())
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"quota.breaks: {item!r} is not a date — ignored")
    return out, warnings


def set_quota_calibration(on: bool) -> None:
    """Turn the statusline calibration samples on or off in config.yaml."""
    config = load_config()
    quota = config.get("quota")
    if not isinstance(quota, dict):
        quota = {}
    quota["calibration"] = bool(on)
    config["quota"] = quota
    save_config(config)


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
