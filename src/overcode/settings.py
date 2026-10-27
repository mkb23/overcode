"""
Centralized configuration and settings for Overcode.

This module consolidates all configuration constants, paths, and settings
that were previously scattered across multiple modules.

Configuration hierarchy:
1. Environment variables (highest priority)
2. Config file (~/.overcode/config.yaml)
3. Default values (lowest priority)

TODO: Make INTERVAL_FAST/SLOW/IDLE configurable via config.yaml
"""

import dataclasses
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Set, Tuple, Union


from . import paths
from .pricing import BEDROCK_GLOBAL, ModelPricing, bedrock_routing, builtin_pricing

if TYPE_CHECKING:
    from .energy import EnergyConfig

# =============================================================================
# Version - increment when daemon code changes significantly
# =============================================================================

DAEMON_VERSION = 3  # Increment when daemon behavior changes (3: engine.sock, 0.6.0)


# =============================================================================
# Daemon Settings
# =============================================================================

@dataclass
class DaemonSettings:
    """Settings for the daemon."""

    # Polling intervals (seconds)
    interval_fast: int = 2       # When active or agents working
    interval_slow: int = 300     # When all agents need user input (5 min)
    interval_idle: int = 3600    # When no agents at all (1 hour)
    # Loop interval while nobody is watching: no client attached to the
    # agents tmux session, no fresh TUI keypress heartbeat and no TUI
    # touching its attended file (config.yaml
    # monitor_daemon.interval_unattended_seconds overrides). Status history
    # is written on change, so the timeline stays gap-free at this
    # resolution; heartbeats and oversight timeouts are wall-clock.
    interval_unattended: int = 10

    # Daemon Claude settings
    daemon_claude_timeout: int = 300  # Max wait for daemon claude (5 min)
    daemon_claude_poll: int = 5       # Poll interval for daemon claude

    # How long a terminated session stays in sessions.json before the
    # monitor daemon moves it to the archive (config.yaml
    # session_archive.terminated_grace_seconds overrides; negative = never).
    terminated_archive_grace_seconds: int = 3600

    # agent_status_history.csv is written on change (audit R10): a row when
    # an agent's (status, activity) pair moves, and otherwise one keepalive
    # row per this many seconds, so a reader can tell "unchanged" from "no
    # daemon" and every live agent has a row within a keepalive of any
    # window's cutoff.
    status_history_keepalive_seconds: int = 60

    # Default tmux session name
    default_tmux_session: str = "agents"


# Global daemon settings
DAEMON = DaemonSettings()


# =============================================================================
# Presence Logger Settings
# =============================================================================

# Global presence settings


# =============================================================================
# TUI Settings
# =============================================================================

# Global TUI settings


# =============================================================================
# Config File Loading
# =============================================================================


def _clamp_fraction(value) -> float:
    """Coerce a config value to a discount fraction in [0.0, 1.0).

    Accepts a number; values outside the range are clamped (a 100%+ discount
    would zero out cost, so we cap just below 1.0). Non-numeric -> 0.0.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f >= 1.0:
        return 0.9999
    return f


def _bedrock_discount_for(model: str | None, fallback: "UserConfig") -> float:
    """Resolve the bedrock discount fraction for a model.

    A per-model override (matched longest-key-first, like pricing) wins over
    the flat ``bedrock.discount``.
    """
    if model and fallback.bedrock_model_discount:
        model_lower = model.lower()
        for key in sorted(fallback.bedrock_model_discount, key=len, reverse=True):
            if key in model_lower:
                return fallback.bedrock_model_discount[key]
    return fallback.bedrock_discount


def _apply_discount(pricing: ModelPricing, fraction: float) -> ModelPricing:
    """Return a copy of ``pricing`` with every rate scaled by (1 - fraction)."""
    if not fraction:
        return pricing
    return pricing.scaled(1.0 - fraction)


def bedrock_routing_of(fallback: "UserConfig", recorded: str | None = None) -> str:
    """A Bedrock agent's routing: the one recorded at launch, else config's.

    Config's is ``bedrock.routing`` when set, else what Claude Code derives
    from ``bedrock.region`` (``pricing.bedrock_routing``).
    """
    if recorded:
        return recorded
    return bedrock_routing(prefix=fallback.bedrock_routing, region=fallback.bedrock_region)


def get_model_pricing(
    model: str | None,
    fallback: "UserConfig",
    provider: str | None = None,
    routing: str | None = None,
) -> ModelPricing:
    """Look up pricing for a model name, falling back to global config.

    User overrides in config.yaml under ``model_pricing:`` first (substring,
    longest key first), then the built-in price table's row for the exact
    model id, the family rows and the models.dev snapshot
    (``pricing.builtin_pricing``), then the global ``pricing:`` rates.

    When ``provider == "bedrock"`` the list rates become what AWS bills:
    times the model's regional premium unless the agent's requests go
    through a ``global.`` profile (``routing``, recorded at launch; else
    config's, ``bedrock_routing_of``), then less any configured bedrock
    discount (flat or per-model). A ``model_pricing:`` row marked
    ``net: true`` is already the price paid and is taken as it is: the
    discount applies once, never on top of a net rate.
    """
    base = _get_list_pricing(model, fallback)
    if provider == "bedrock" and not base.net:
        if bedrock_routing_of(fallback, routing) != BEDROCK_GLOBAL:
            base = base.scaled(base.regional if base.regional is not None else 1.0)
        return _apply_discount(base, _bedrock_discount_for(model, fallback))
    return base


def _get_list_pricing(model: str | None, fallback: "UserConfig") -> ModelPricing:
    """Resolve list (pre-discount) pricing for a model name.

    Order: config.yaml ``model_pricing`` overrides (substring, longest key
    first), then the built-in price table's row, family rows and models.dev
    snapshot (``pricing.builtin_pricing``), then the configured default
    per-token rates. A user row the user wrote without ``regional`` takes
    the built-in row's, so a list price override keeps Bedrock's premium.
    """
    if model:
        model_lower = model.lower()
        for key in sorted(fallback.model_pricing, key=len, reverse=True):
            if key in model_lower:
                user = fallback.model_pricing[key]
                if user.regional is None:
                    builtin = builtin_pricing(model)
                    user = replace(user, regional=builtin.regional if builtin else 1.0)
                return user
        builtin = builtin_pricing(model)
        if builtin is not None:
            return builtin
    return ModelPricing(
        input=fallback.price_input,
        output=fallback.price_output,
        cache_write=fallback.price_cache_write,
        cache_read=fallback.price_cache_read,
        cache_write_1h=fallback.price_cache_write_1h,
    )


def _optional_price(value) -> "float | None":
    """A configured price, or None when unset or not a non-negative number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _default_energy_config():
    from .energy import EnergyConfig
    return EnergyConfig()


@dataclass
class UserConfig:
    """User-configurable settings from config.yaml."""

    default_standing_instructions: str = ""
    tmux_session: str = "agents"

    # Token pricing (per million tokens) - defaults to Sonnet
    price_input: float = 3.0        # $/MTok for input tokens
    price_output: float = 15.0      # $/MTok for output tokens
    price_cache_write: float = 3.75  # $/MTok for cache creation
    # $/MTok for 1-hour TTL cache writes; None: twice the input price
    price_cache_write_1h: Optional[float] = None
    price_cache_read: float = 0.30   # $/MTok for cache reads

    # Per-model pricing overrides (loaded from config.yaml model_pricing section)
    model_pricing: dict = field(default_factory=dict)

    # Bedrock discount (loaded from config.yaml bedrock section). Bedrock's
    # global-profile on-demand prices equal the Anthropic API list prices of
    # the price table; geographic and in-region profiles cost the model's
    # ``regional`` multiple (1.1). Enterprise customers negotiate a private-
    # offer / committed-use discount off that (commonly a flat % across all
    # models, sometimes per-model). Applied only to provider == "bedrock"
    # sessions, once. For anything a flat % can't express (input/output-
    # asymmetric net rates, provisioned throughput), set exact net rates via
    # model_pricing with ``net: true``.
    bedrock_discount: float = 0.0          # fraction off list, e.g. 0.30 = 30% off
    bedrock_model_discount: dict = field(default_factory=dict)  # {family: fraction}
    # bedrock.routing: "global" or a geography prefix, for agents whose
    # routing was not recorded at launch; None: derived from bedrock.region
    bedrock_routing: Optional[str] = None
    bedrock_region: str = "us-east-1"

    # Skill emoji overrides (loaded from config.yaml skill_emoji section) (#252)
    skill_emoji: dict = field(default_factory=dict)

    # Wrapper emoji overrides (loaded from config.yaml wrapper_emoji section) (#437)
    wrapper_emoji: dict = field(default_factory=dict)

    # Burn-rate color thresholds (#174). USD/hr cutoffs, sorted ascending —
    # rates below the lowest cutoff get its color, etc., above all cutoffs
    # gets red. Color names must be valid Rich colors (e.g. "orange1", not
    # plain "orange", which Rich doesn't recognize).
    # Energy estimate coefficients (#522), from config.yaml's energy section;
    # see energy.py for the model and the defaults
    energy: "EnergyConfig" = field(default_factory=lambda: _default_energy_config())

    burn_thresholds: list = field(default_factory=lambda: [
        ("green", 1.0),
        ("yellow", 10.0),
        ("orange1", 100.0),
        # implicit: ≥ 100 → red
    ])

    @classmethod
    def load(cls) -> "UserConfig":
        """The settings in config.yaml now (config's shared, stat-gated parse)."""
        from .config import _cached_config
        return cls.from_dict(_cached_config())

    @classmethod
    def from_dict(cls, data) -> "UserConfig":
        """Settings from a parsed config.yaml mapping; defaults for anything else."""
        if not isinstance(data, dict):
            return cls()

        # Load pricing config (nested under 'pricing' key)
        pricing = data.get("pricing", {})
        if not isinstance(pricing, dict):
            pricing = {}

        # Parse per-model pricing overrides
        model_pricing_raw = data.get("model_pricing", {})
        model_pricing_parsed: dict[str, ModelPricing] = {}
        if isinstance(model_pricing_raw, dict):
            for key, vals in model_pricing_raw.items():
                if isinstance(vals, dict):
                    model_pricing_parsed[key] = ModelPricing(
                        input=vals.get("input", 3.0),
                        output=vals.get("output", 15.0),
                        cache_write=vals.get("cache_write", 3.75),
                        cache_read=vals.get("cache_read", 0.30),
                        cache_write_1h=_optional_price(vals.get("cache_write_1h")),
                        # None: the built-in row's (_get_list_pricing)
                        regional=_optional_price(vals.get("regional")),
                        net=vals.get("net") is True,
                        source=f"config: model_pricing.{key}",
                    )

        # Parse bedrock discount (flat + optional per-model overrides),
        # the routing override and the region Claude Code derives it from
        bedrock_raw = data.get("bedrock", {})
        bedrock_discount = 0.0
        bedrock_model_discount: dict[str, float] = {}
        bedrock_routing_cfg: Optional[str] = None
        bedrock_region = "us-east-1"
        if isinstance(bedrock_raw, dict):
            bedrock_discount = _clamp_fraction(bedrock_raw.get("discount", 0.0))
            md_raw = bedrock_raw.get("model_discount", {})
            if isinstance(md_raw, dict):
                bedrock_model_discount = {
                    str(k).lower(): _clamp_fraction(v) for k, v in md_raw.items()
                }
            if isinstance(bedrock_raw.get("routing"), str):
                bedrock_routing_cfg = bedrock_raw["routing"]
            if isinstance(bedrock_raw.get("region"), str) and bedrock_raw["region"]:
                bedrock_region = bedrock_raw["region"]

        # Parse skill emoji overrides (#252)
        skill_emoji_raw = data.get("skill_emoji", {})
        skill_emoji_parsed = (
            {str(k): str(v) for k, v in skill_emoji_raw.items()}
            if isinstance(skill_emoji_raw, dict) else {}
        )

        # Parse wrapper emoji overrides (#437)
        wrapper_emoji_raw = data.get("wrapper_emoji", {})
        wrapper_emoji_parsed = (
            {str(k): str(v) for k, v in wrapper_emoji_raw.items()}
            if isinstance(wrapper_emoji_raw, dict) else {}
        )

        # Parse burn-rate color thresholds (#174). Accepts either
        #   burn_thresholds: {green: 1.0, yellow: 10.0, orange: 100.0}
        # or a list of {color: ..., max_usd_per_hour: ...} entries.
        # The fallback list is set in the dataclass default.
        burn_raw = data.get("burn_thresholds")
        burn_parsed = None
        if isinstance(burn_raw, dict):
            pairs = []
            for color, cutoff in burn_raw.items():
                try:
                    pairs.append((str(color), float(cutoff)))
                except (TypeError, ValueError):
                    continue
            pairs.sort(key=lambda p: p[1])
            if pairs:
                burn_parsed = pairs
        elif isinstance(burn_raw, list):
            pairs = []
            for entry in burn_raw:
                if not isinstance(entry, dict):
                    continue
                color = entry.get("color")
                cutoff = entry.get("max_usd_per_hour", entry.get("cutoff"))
                if color is None or cutoff is None:
                    continue
                try:
                    pairs.append((str(color), float(cutoff)))
                except (TypeError, ValueError):
                    continue
            pairs.sort(key=lambda p: p[1])
            if pairs:
                burn_parsed = pairs

        kwargs = dict(
            default_standing_instructions=data.get(
                "default_standing_instructions", ""
            ),
            tmux_session=data.get("tmux_session", "agents"),
            price_input=pricing.get("input", 3.0),
            price_output=pricing.get("output", 15.0),
            price_cache_write=pricing.get("cache_write", 3.75),
            price_cache_write_1h=_optional_price(pricing.get("cache_write_1h")),
            price_cache_read=pricing.get("cache_read", 0.30),
            model_pricing=model_pricing_parsed,
            bedrock_discount=bedrock_discount,
            bedrock_model_discount=bedrock_model_discount,
            bedrock_routing=bedrock_routing_cfg,
            bedrock_region=bedrock_region,
            skill_emoji=skill_emoji_parsed,
            wrapper_emoji=wrapper_emoji_parsed,
        )
        if burn_parsed is not None:
            kwargs["burn_thresholds"] = burn_parsed
        from .energy import EnergyConfig
        kwargs["energy"] = EnergyConfig.from_dict(data.get("energy"))
        return cls(**kwargs)


# The UserConfig built from config's shared parse, and that parse: rebuilt
# when config.yaml changes (a new parse is a new object), never otherwise.
_user_config: Optional[UserConfig] = None
_user_config_source: Optional[dict] = None


def get_user_config() -> UserConfig:
    """The user configuration, current with config.yaml (one stat per call)."""
    global _user_config, _user_config_source
    from .config import _cached_config
    data = _cached_config()
    if _user_config is None or data is not _user_config_source:
        _user_config, _user_config_source = UserConfig.from_dict(data), data
    return _user_config


# =============================================================================
# Session-Specific Paths
# =============================================================================

def get_session_dir(session: str) -> Path:
    """Get the directory for session-specific files.

    Each overcode session (tmux session) gets its own subdirectory
    for isolation. This allows running multiple overcode instances
    (e.g., one for work, one for development).

    Under the sessions root (``OVERCODE_STATE_DIR``; see ``paths``).
    """
    return paths.session_dir(session)


def get_monitor_daemon_pid_path(session: str) -> Path:
    """Get monitor daemon PID file path for a specific session."""
    return get_session_dir(session) / "monitor_daemon.pid"


def get_monitor_daemon_state_path(session: str) -> Path:
    """Get monitor daemon state file path for a specific session."""
    return get_session_dir(session) / "monitor_daemon_state.json"


def get_supervisor_daemon_pid_path(session: str) -> Path:
    """Get supervisor daemon PID file path for a specific session."""
    return get_session_dir(session) / "supervisor_daemon.pid"


def get_agent_history_path(session: str) -> Path:
    """Get agent status history CSV path for a specific session."""
    return get_session_dir(session) / "agent_status_history.csv"


def get_swarm_history_path(session: str) -> Path:
    """Swarm counts history CSV (the timeline's density rows) for a session."""
    return get_session_dir(session) / "swarm_history.csv"


def get_activity_signal_path(session: str) -> Path:
    """Get activity signal file path for a specific session."""
    return get_session_dir(session) / "activity_signal"


def signal_activity(session: str = None) -> None:
    """Signal user activity to the daemon (called by TUI on keypress).

    Creates a signal file that the daemon checks each loop.
    When it sees this file, it wakes up and runs immediately.
    This provides responsiveness when users interact with TUI.
    """
    if session is None:
        session = DAEMON.default_tmux_session
    signal_path = get_activity_signal_path(session)
    try:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.touch()
    except OSError:
        pass  # Best effort


def get_supervisor_stats_path(session: str) -> Path:
    """Get supervisor stats file path for a specific session."""
    return get_session_dir(session) / "supervisor_stats.json"


def get_supervisor_log_path(session: str) -> Path:
    """Get supervisor log file path for a specific session."""
    return get_session_dir(session) / "supervisor.log"


def get_web_server_pid_path(session: str) -> Path:
    """Get API server PID file path for a specific session."""
    return get_session_dir(session) / "web_server.pid"


def get_web_server_port_path(session: str) -> Path:
    """Get API server port file path for a specific session."""
    return get_session_dir(session) / "web_server.port"


def get_detection_mode_path(session: str) -> Path:
    """Get the detection mode file path for a specific session."""
    return get_session_dir(session) / "detection_mode"


def read_detection_mode(session: str) -> str:
    """Read the global detection mode ('hooks' or 'polling').

    Returns 'auto' if no explicit mode is set (auto-detect on startup).
    """
    try:
        return get_detection_mode_path(session).read_text().strip()
    except (FileNotFoundError, IOError):
        return "auto"


def resolve_detection_mode(
    session: str, live_names: Optional[Iterable[Union[str, Tuple[str, str]]]] = None
) -> str:
    """Resolve 'auto' to 'hooks' or 'polling' (#TBD).

    Detection order:
      1. Explicit user choice ('hooks' or 'polling' written to disk).
      2. A live agent of this session with a hook_state file: proof that
         hooks fire, regardless of whether the user-level Claude config has
         them. Overcode-launched sessions inject hooks via `--settings` JSON
         inline, so the user-level config check below misses them.
      3. User-level Claude config (legacy fallback for manually-run
         Claude Code sessions that read ~/.claude/settings.json).

    ``live_names`` are the session's live agents, as (session id, name)
    pairs or bare names (the daemon passes its tick's); without them
    sessions.json is read.
    """
    mode = read_detection_mode(session)
    if mode in ("hooks", "polling"):
        return mode
    if _session_has_hook_activity(session, live_names):
        return "hooks"
    from .claude_config import ClaudeConfigEditor
    return "hooks" if ClaudeConfigEditor.are_overcode_hooks_installed() else "polling"


def _live_agent_names(session: str) -> List[Tuple[str, str]]:
    from .session_manager import SessionManager

    return [(s.id, s.name) for s in SessionManager().list_sessions()
            if s.tmux_session == session]


def _session_has_hook_activity(
    session: str, live_names: Optional[Iterable[Union[str, Tuple[str, str]]]] = None
) -> bool:
    """True if a live agent of ``session`` has a hook_state file.

    Stats one known path per live agent, never lists the directory: hook
    files of finished agents pile up there (thousands for a heavy fan-out
    user), and a listing every tick cost 140 ms at 15K of them. A quiet
    live agent still counts: its hooks fired, they just have nothing to say.
    """
    if live_names is None:
        live_names = _live_agent_names(session)
    session_dir = get_session_dir(session)
    for live in live_names:
        sid, name = live if isinstance(live, tuple) else (None, live)
        if paths.agent_path(session, sid, name, "hook_state", base=session_dir).exists():
            return True
    return False


def get_tui_log_path(session: str) -> Path:
    """Get the TUI diagnostic log file path for a specific session."""
    return get_session_dir(session) / "tui.log"


def get_diagnostics_dir(session: str) -> Path:
    """Get the diagnostics directory for a specific session."""
    return get_session_dir(session) / "diagnostics"


def get_event_loop_timing_path(session: str) -> Path:
    """Get the event loop timing CSV path for a specific session."""
    return get_diagnostics_dir(session) / "event_loop_timing.csv"


def get_status_changes_path(session: str) -> Path:
    """Get the status changes diagnostic CSV path for a specific session."""
    return get_diagnostics_dir(session) / "status_changes.csv"


def cap_diagnostics_csv(path: Path, max_mb: float) -> bool:
    """Hard cap a diagnostic CSV (#465): drop the oldest 90% once it exceeds max_mb.

    Diagnostic CSVs like diagnostics/event_loop_timing.csv are append-only
    debug output, not archival data — unlike agent_status_history.csv there's
    no compress-and-retain step, just a backstop truncation so a busy TUI
    session can't fill the disk. Assumes a single-line CSV header.

    Returns True if truncation occurred.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_mb * 1024 * 1024:
        return False
    try:
        with open(path, 'rb') as f:
            header = f.readline()
            f.seek(int(size * 0.9))
            f.readline()  # discard partial line at the seek point
            tail = f.read()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, 'wb') as f:
            f.write(header)
            f.write(tail)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def ensure_session_dir(session: str) -> Path:
    """Ensure session directory exists and return it."""
    session_dir = get_session_dir(session)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# =============================================================================
# TUI Preferences (persisted between launches)
# =============================================================================

def get_tui_heartbeat_path(session: str) -> Path:
    """Get TUI heartbeat file path for a specific session."""
    return get_session_dir(session) / "tui_heartbeat"


def write_tui_heartbeat(session: str) -> None:
    """Write current ISO timestamp to TUI heartbeat file.

    Called by TUI on keypress (throttled) so the monitor daemon
    can distinguish TUI-active from computer-active presence.
    """
    from datetime import datetime
    heartbeat_path = get_tui_heartbeat_path(session)
    try:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(datetime.now().isoformat())
    except OSError:
        pass  # Best effort


# The TUI touches this file every TUI_ATTENDED_TOUCH_SECONDS while a tmux
# client is attached to the pane it runs in, and the API server touches it
# whenever it serves a status request (a sister TUI's poll). It is the
# monitor daemon's third "someone is watching" signal, next to the keypress
# heartbeat above and the attached count of the agents session: the split
# (which lives in the `overcode` session), a sister — both are invisible to
# those two, and without this
# touch the daemon would stretch to its unattended interval while a sister
# is watching. A touch is one utime, no content.
TUI_ATTENDED_TOUCH_SECONDS = 5


def get_tui_attended_path(session: str) -> Path:
    """Path of the TUI's attended-liveness touch file for ``session``."""
    return get_session_dir(session) / "tui_attended"


def touch_tui_attended(session: str) -> None:
    """Stamp the attended file's mtime to now (best effort)."""
    path = get_tui_attended_path(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass  # Best effort


def tui_attended_age_seconds(session: str, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the attended file was last touched; None when there is none."""
    import time as _time

    try:
        mtime = get_tui_attended_path(session).stat().st_mtime
    except OSError:
        return None
    return (now if now is not None else _time.time()) - mtime


def get_tui_preferences_path(session: str) -> Path:
    """Get TUI preferences file path for a specific session."""
    return get_session_dir(session) / "tui_preferences.json"


@dataclass
class TUIPreferences:
    """TUI preferences that persist between launches."""

    # Fields excluded from persistence — always use dataclass default. (The
    # old "show_done" key, not persisted since #319, is ignored on load:
    # done children are remembered as show_done_children since 2026-10-18.)
    _SKIP_PERSIST = frozenset()
    # Load defaults that differ from dataclass defaults (migration compat)
    _LOAD_DEFAULTS = {"baseline_minutes": 0}

    summary_detail: str = "full"  # low, med, full
    timeline_visible: bool = True
    daemon_panel_visible: bool = False
    tui_log_panel_visible: bool = False
    show_terminated: bool = False  # keep killed sessions visible in timeline
    hide_asleep: bool = False  # hide sleeping agents from display
    # Show "done" child agents (#244; the Agent visibility dialog). A new
    # key, so a stale "show_done": true saved before #319 stays ignored
    show_done_children: bool = False
    sort_mode: str = "alphabetical"  # alphabetical, by_status, by_value, by_tree (#61), col:<id> (#487), by_tag (#538)
    sort_reversed: bool = False  # flip the sort column's natural direction (#487)
    summary_content_mode: str = "ai_short"  # SupervisorTUI.SUMMARY_CONTENT_MODES (#98, #171)
    baseline_minutes: int = 60  # 0=now (instantaneous), 15/30/.../180 = minutes back for mean spin
    monochrome: bool = False  # B&W mode for terminals with ANSI issues (#138)
    emoji_free: bool = False  # ASCII fallbacks for terminals without emoji (#315)
    theme: str = "dark"  # "dark" | "light" colour theme (#508)
    show_cost: str = "tokens"  # "tokens", "cost", "joules" — cycle with $
    timeline_hours: float = 3.0  # 1, 3, 6, 12, 24 — timeline scope (#191)
    notifications: str = "off"  # "off", "sound", "banner", "both" — macOS notifications (#235)
    # (Visits to stalled agents are the engine's since 0.6.0: an older file's
    # visited_stalled_agents / visited_stalled_at keys are ignored.)
    # Visits to sister agents ({remote session id: epoch}): no engine here
    # records them, so they persist with the TUI and the 🔔 a visit cleared
    # stays cleared across a restart
    remote_visits: dict = field(default_factory=dict)
    # Per-level column overrides: {"low": {"uptime": true, ...}, "med": {...}, "high": {...}}
    # Only stores explicit user overrides. Missing = use default from detail_levels.
    column_config: dict = field(default_factory=dict)
    # Show abbreviated column headers above summary lines
    show_column_headers: bool = True
    # Sister instances hidden from agent list (#323)
    disabled_sisters: Set[str] = field(default_factory=set)
    # Parents whose children are folded in tree sort mode (X key, #244),
    # persisted so a fold survives a TUI restart (#464)
    collapsed_parents: Set[str] = field(default_factory=set)
    # Delegated work (0.6.1 observability): the swarm-detail rule (f:
    # "off" | "groups" | "needs_you"), per-agent overrides of it ({agent id:
    # expanded?}, from → / ← / X) and opened "+k more" lists ({row key:
    # open?}); ids of agents that are gone are pruned on save
    swarm_detail: str = "off"
    swarm_expanded: dict = field(default_factory=dict)
    swarm_opened: dict = field(default_factory=dict)
    # What finished delegated agents show as: "show" (a row for a while and
    # ✅/❌ counts), "recent" (the same, ✅ only for the last few hours),
    # "failed" (only the ❌ ones) or "hide" (visibility dialog)
    swarm_finished: str = "recent"
    # The agent last highlighted by hand (its id): the next start highlights
    # it again, and the split's bottom pane opens on its window
    last_focused_agent: str = ""
    # Actions last run from the command palette, newest first (#482)
    recent_commands: List[str] = field(default_factory=list)
    # Log every status change to diagnostics CSV (off by default)
    status_change_logging: bool = False

    @classmethod
    def load(cls, session: str) -> "TUIPreferences":
        """Load TUI preferences from file."""
        import json
        prefs_path = get_tui_preferences_path(session)

        if not prefs_path.exists():
            return cls()

        try:
            with open(prefs_path) as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return cls()

                kwargs = {}
                for fld in dataclasses.fields(cls):
                    if fld.name in cls._SKIP_PERSIST:
                        continue
                    # Use migration default if specified, else dataclass default
                    if fld.name in cls._LOAD_DEFAULTS:
                        default = cls._LOAD_DEFAULTS[fld.name]
                    elif fld.default is not dataclasses.MISSING:
                        default = fld.default
                    else:
                        default = fld.default_factory()
                    value = data.get(fld.name, default)
                    # Convert JSON lists back to sets
                    if fld.default_factory is set:
                        value = set(value) if value else set()
                    kwargs[fld.name] = value

                # Migration: map "custom" detail level to "full"
                if kwargs.get("summary_detail") == "custom":
                    kwargs["summary_detail"] = "full"

                # Migration: show_cost bool → str ("tokens"/"cost"/"joules")
                sc = kwargs.get("show_cost")
                if sc is True:
                    kwargs["show_cost"] = "cost"
                elif sc is False:
                    kwargs["show_cost"] = "tokens"

                # Migration: show_cost str → column_config overrides (#416)
                sc = kwargs.get("show_cost", "tokens")
                col_cfg = kwargs.setdefault("column_config", {})
                if sc in ("cost", "joules") and not any(
                    col_cfg.get(lvl, {}).get("cost") is not None
                    for lvl in ("low", "med", "high", "full")
                ):
                    preset = {
                        "cost": {"token_count": False, "cost": True, "joules": False,
                                 "budget": True, "subtree_cost": True},
                        "joules": {"token_count": False, "cost": False, "joules": True,
                                   "budget": False, "subtree_cost": True},
                    }.get(sc, {})
                    for lvl in ("low", "med", "high", "full"):
                        col_cfg.setdefault(lvl, {}).update(preset)

                # Migration: merged or renamed columns (swarm / subagent_count
                # → subagents, 0.6.1) keep their overrides and sort; a removed
                # one's (task_result, 0.6.1) are dropped
                from .columns.registry import (
                    REMOVED_COLUMNS, canonical_column_id, migrate_column_overrides,
                )
                if isinstance(col_cfg, dict):
                    for lvl, overrides in list(col_cfg.items()):
                        col_cfg[lvl] = migrate_column_overrides(overrides)
                sort_mode = kwargs.get("sort_mode")
                if isinstance(sort_mode, str) and sort_mode.startswith("col:"):
                    if sort_mode[4:] in REMOVED_COLUMNS:
                        kwargs.pop("sort_mode")
                    else:
                        kwargs["sort_mode"] = "col:" + canonical_column_id(sort_mode[4:])

                return cls(**kwargs)
        except (json.JSONDecodeError, IOError):
            return cls()

    def save(self, session: str) -> None:
        """Save TUI preferences to file."""
        import json
        prefs_path = get_tui_preferences_path(session)

        try:
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for fld in dataclasses.fields(self):
                if fld.name in self._SKIP_PERSIST:
                    continue
                value = getattr(self, fld.name)
                if isinstance(value, set):
                    value = sorted(value)
                data[fld.name] = value
            with open(prefs_path, 'w') as f:
                json.dump(data, f, indent=2)
        except (IOError, OSError):
            pass  # Best effort
