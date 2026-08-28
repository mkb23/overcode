"""codex stats: the rollout-JSONL reader behind ``StatsReader``.

Phase 2 of ``docs/design/agent-backends-codex-grok.md`` (§2.4, Appendix A).
Unlike opencode's SQLite store, codex keeps one append-only JSONL file per
conversation:

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl

Line 1 is always ``{"type":"session_meta","payload":{"id","cwd",...}}``.
Everything else this reader cares about is one of three ``type``s:

    event_msg (payload.type == "token_count")
        payload.info.total_token_usage: input_tokens, cached_input_tokens,
        cache_write_input_tokens, output_tokens, reasoning_output_tokens,
        total_tokens (+ model_context_window). This is a *running total*
        snapshot, not a delta, so the reader keeps only the newest one seen.
    turn_context
        payload.model (also duplicated at
        payload.collaboration_mode.settings.model) — the "current model"
        signal; one line per turn, latest wins.
    response_item (payload.type == "message", payload.role == "user")
        counted as a real interaction only when
        payload.internal_chat_message_metadata_passthrough.content_item_kinds
        contains "user.text" — this excludes injected
        <environment_context>/skills/permissions scaffolding, which carries
        its own kind tags instead (Phase 0's more-robust-than-string-
        matching finding, §2.4).

Discovery: primary lookup is by the session id codex's ``SessionStart`` hook
recorded into ``hook_state_<agent>.json`` (``agent_session_ids`` /
``agent_session_id`` — see ``hook_handler.write_hook_state``); fallback is a
``session_meta.cwd`` match within a bounded window of day-directories around
the agent's launch time, mirroring ``OpencodeStatsReader``'s directory+time
fallback. Both are bounded to a few calendar days either side of the launch
date — never a full-history scan.

Same defensive posture as ``OpencodeStatsReader`` throughout: read-only
(files are only ever opened for read), any surprise (missing directory,
corrupt line, absent field) degrades to None/empty rather than raising into
a daemon tick, and a shape that doesn't match what this reader expects is a
``schema_findings()`` doctor warning, not a crash.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..stats_reader import (
    AgentSessionStats,
    DiscoveredSessionIds,
    empty_window_usage,
)

# How many calendar days either side of a timestamp to scan when locating
# rollout files by date. Bounded so a fallback cwd+time match never turns
# into a scan of the user's entire ~/.codex/sessions history.
_DAY_SPAN = 1

# Keys expected inside a token_count event's total_token_usage object.
# Absence of all of these from the newest rollout file's token_count event is
# schema drift, not "codex hasn't produced one yet" (see schema_findings()).
_EXPECTED_TOKEN_USAGE_KEYS: tuple = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def codex_home() -> Path:
    """codex's config/state root, honouring CODEX_HOME (the ``-p/--profile``
    env var codex itself documents, per Appendix A)."""
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def sessions_root() -> Path:
    """Where codex writes rollout JSONL files, day-bucketed."""
    return codex_home() / "sessions"


def _day_dirs(root: Path, around: Optional[datetime], span: int = _DAY_SPAN) -> List[Path]:
    """Candidate ``YYYY/MM/DD`` directories near ``around``, closest first."""
    if around is None:
        return []
    offsets = sorted(range(-span, span + 1), key=abs)  # 0, -1, 1, -2, 2, ...
    dirs: List[Path] = []
    for offset in offsets:
        day = around + timedelta(days=offset)
        dirs.append(root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}")
    return dirs


def _rollout_files_in(day_dir: Path) -> List[Path]:
    try:
        return sorted(p for p in day_dir.glob("rollout-*.jsonl") if p.is_file())
    except OSError:
        return []


def _iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (ValueError, TypeError):
                    continue
    except OSError:
        return


def _read_session_meta(path: Path) -> Optional[dict]:
    """The first line's payload, or None when it isn't a session_meta record."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return None
    try:
        entry = json.loads(first_line)
    except (ValueError, TypeError):
        return None
    if not isinstance(entry, dict) or entry.get("type") != "session_meta":
        return None
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _launch_datetime(session: Any) -> Optional[datetime]:
    start_time = getattr(session, "start_time", None)
    if not start_time:
        return None
    try:
        return datetime.fromisoformat(start_time)
    except (ValueError, TypeError):
        return None


def _scan_rollout(path: Path) -> Dict[str, Any]:
    """One pass over a rollout file for the fields the stats columns need.

    ``token_count`` events carry a running total, not a delta, so later
    events simply overwrite earlier ones — the last one read in file order
    is "the latest", matching §2.4's "latest total_token_usage" mapping.
    """
    out: Dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "current_context_tokens": 0,
        "model": None,
        "interaction_count": 0,
        "model_context_window": None,
    }
    for entry in _iter_jsonl(path):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        etype = entry.get("type")

        if etype == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(usage, dict):
                out["input_tokens"] = _as_int(usage.get("input_tokens"))
                # codex bills reasoning as output and reports it separately
                # (matching the opencode convention this reader follows
                # elsewhere) — folded into output rather than dropped.
                out["output_tokens"] = _as_int(usage.get("output_tokens")) + _as_int(
                    usage.get("reasoning_output_tokens")
                )
                out["cache_read_tokens"] = _as_int(usage.get("cached_input_tokens"))
                out["cache_write_tokens"] = _as_int(usage.get("cache_write_input_tokens"))
            # Context occupancy comes from last_token_usage (the latest
            # request: its input already contains the whole conversation,
            # plus its output), NOT total_token_usage — the cumulative
            # totals re-count the resent context every turn, so a tiny
            # two-turn session read as 2x its real window usage (29.1K vs
            # codex's own "/status: 14.5K used"). total_token_usage keeps
            # feeding the Σ token columns above, where cumulative is the
            # point. Falls back to the cumulative figure only when
            # last_token_usage is absent (single-turn files: identical).
            last = info.get("last_token_usage") if isinstance(info, dict) else None
            if isinstance(last, dict) and last.get("total_tokens") is not None:
                out["current_context_tokens"] = _as_int(last.get("total_tokens"))
            elif isinstance(usage, dict):
                out["current_context_tokens"] = _as_int(usage.get("total_tokens"))
            # model_context_window is a sibling of total_token_usage inside
            # `info`, not nested inside it (#469) — codex's own CLI reports
            # this per token_count event; a running total like the usage
            # fields, so "latest wins" here too. Preferred by
            # AgentSessionStats.max_context_tokens over the static
            # history_reader.MODEL_CONTEXT_WINDOWS table when present.
            if isinstance(info, dict):
                window = _as_int(info.get("model_context_window"))
                if window > 0:
                    out["model_context_window"] = window
            continue

        if etype == "turn_context":
            model = payload.get("model")
            if not model:
                collab = payload.get("collaboration_mode")
                settings = collab.get("settings") if isinstance(collab, dict) else None
                if isinstance(settings, dict):
                    model = settings.get("model")
            if model:
                out["model"] = model
            continue

        if etype == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            meta = payload.get("internal_chat_message_metadata_passthrough")
            kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
            if isinstance(kinds, list) and "user.text" in kinds:
                out["interaction_count"] += 1
            continue

    return out


def _hook_state_path(session: Any) -> Optional[Path]:
    """Where hook_handler.py publishes this agent's hook state."""
    tmux_session = getattr(session, "tmux_session", None)
    name = getattr(session, "name", None)
    if not tmux_session or not name:
        return None
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    base = Path(state_dir) if state_dir else Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_state_{name}.json"


def session_ids_from_hook_state(session: Any) -> List[str]:
    """codex session ids codex's SessionStart hook recorded, newest last.

    Exact analogue of ``opencode_stats.session_ids_from_hook_state`` — the
    field names (``agent_session_ids`` / ``agent_session_id``) are the same
    because ``hook_handler.write_hook_state`` writes them the same way for
    every backend that supplies a ``session_id``.
    """
    path = _hook_state_path(session)
    if path is None:
        return []
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []

    ids: List[str] = []
    raw_ids = state.get("agent_session_ids")
    if isinstance(raw_ids, list):
        ids.extend(sid for sid in raw_ids if isinstance(sid, str) and sid)
    active = state.get("agent_session_id")
    if isinstance(active, str) and active:
        ids = [sid for sid in ids if sid != active] + [active]
    return ids


def schema_findings() -> List[str]:
    """Doctor warning when the newest rollout file's token_count shape has drifted.

    Empty when there is nothing to check (no sessions yet — not a fault) or
    the shape matches. Best-effort: only the most recently modified rollout
    file within a couple of days of "now" is inspected, since a full scan of
    every session ever recorded would be far too expensive for a doctor pass.
    """
    latest = _latest_rollout_file()
    if latest is None:
        return []
    usage_keys: Optional[set] = None
    try:
        for entry in _iter_jsonl(latest):
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict) or entry.get("type") != "event_msg":
                continue
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(usage, dict):
                usage_keys = set(usage.keys())
                break
    except OSError:
        return []
    if not usage_keys:
        # No token_count event seen yet in this file — likely just an early
        # session, not drift.
        return []
    missing = [key for key in _EXPECTED_TOKEN_USAGE_KEYS if key not in usage_keys]
    if not missing:
        return []
    return [
        "codex's rollout JSONL token_count shape has drifted — missing "
        + ", ".join(missing)
        + f" in {latest}. Token/cost/context columns will show dashes until "
        "overcode is updated."
    ]


def _latest_rollout_file(root: Optional[Path] = None) -> Optional[Path]:
    base = root or sessions_root()
    candidates: List[Path] = []
    for day_dir in _day_dirs(base, datetime.now(), span=_DAY_SPAN):
        candidates.extend(_rollout_files_in(day_dir))
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


class CodexStatsReader:
    """Reads codex's rollout JSONL for one session.

    Locates the rollout file by the session id codex's ``SessionStart`` hook
    recorded, falling back to a ``session_meta.cwd`` match near the agent's
    launch time when hooks never fired. Any failure — missing directory, a
    corrupt line, an unreadable file — answers "unknown" so the columns
    render dashes instead of zeros.
    """

    backend_name = "codex"

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self._sessions_dir = sessions_dir

    def _root(self) -> Path:
        return self._sessions_dir if self._sessions_dir is not None else sessions_root()

    @staticmethod
    def _owned_ids(session: Any) -> List[str]:
        ids = list(getattr(session, "agent_session_ids", None) or [])
        active = getattr(session, "active_agent_session_id", None)
        if active and active not in ids:
            ids.append(active)
        return [sid for sid in ids if isinstance(sid, str) and sid]

    def _find_by_session_id(self, session_id: str, around: Optional[datetime]) -> Optional[Path]:
        if not session_id:
            return None
        for day_dir in _day_dirs(self._root(), around):
            for path in _rollout_files_in(day_dir):
                if session_id in path.name:
                    return path
        return None

    def _find_by_cwd(self, cwd: str, since: Optional[datetime]) -> List[Path]:
        if since is None:
            return []
        matches: List[Path] = []
        for day_dir in _day_dirs(self._root(), since):
            for path in _rollout_files_in(day_dir):
                meta = _read_session_meta(path)
                if meta and meta.get("cwd") == cwd:
                    matches.append(path)
        # Filenames embed an ISO timestamp right after "rollout-", so a
        # lexical sort is also a chronological one; oldest first.
        matches.sort(key=lambda p: p.name)
        return matches

    def _path_for(self, session: Any) -> Optional[Path]:
        launch_dt = _launch_datetime(session)
        for session_id in reversed(self._owned_ids(session)):
            found = self._find_by_session_id(session_id, launch_dt)
            if found is not None:
                return found
        cwd = getattr(session, "start_directory", None)
        if not cwd:
            return None
        candidates = self._find_by_cwd(str(cwd), launch_dt)
        return candidates[-1] if candidates else None

    # -- StatsReader -------------------------------------------------------

    def get_stats(self, session: Any, *, history_file: Any = None) -> Optional[AgentSessionStats]:
        path = self._path_for(session)
        if path is None:
            return None
        scan = _scan_rollout(path)
        if not scan["model"] and not scan["input_tokens"] and not scan["output_tokens"] and not scan["interaction_count"]:
            # Nothing usable was actually read — an empty/near-empty rollout
            # file reads as "unknown", not an all-zero session.
            return None
        return AgentSessionStats(
            interaction_count=scan["interaction_count"],
            input_tokens=scan["input_tokens"],
            output_tokens=scan["output_tokens"],
            cache_creation_tokens=scan["cache_write_tokens"],
            cache_read_tokens=scan["cache_read_tokens"],
            work_times=[],
            current_context_tokens=scan["current_context_tokens"],
            model=scan["model"],
            # Deliberately None — see OpencodeStatsReader's identical
            # comment: `provider` is overcode's API-transport discriminator,
            # not the model's vendor.
            provider=None,
            reported_context_window=scan["model_context_window"],
        )

    def get_stored_cost(self, session: Any) -> Optional[float]:
        # codex is subscription/API billed with no local per-turn charge
        # recorded (design doc §2.4: "Cost: not stored"), so there is
        # nothing to prefer over pricing.py's recomputation — same posture
        # as ClaudeStatsReader.
        return None

    def get_current_session_id(self, session: Any, since: datetime) -> Optional[str]:
        from_hook = session_ids_from_hook_state(session)
        if from_hook:
            return from_hook[-1]
        cwd = getattr(session, "start_directory", None)
        if not cwd:
            return None
        candidates = self._find_by_cwd(str(cwd), since)
        if not candidates:
            return None
        meta = _read_session_meta(candidates[-1])
        return meta.get("id") if meta else None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        from ..claude_pid import is_session_id_owned_by_others

        owned = set(self._owned_ids(session))
        session_id = getattr(session, "id", None)

        discovered: List[str] = []
        latest: Optional[str] = None

        def consider(sid: Optional[str]) -> None:
            nonlocal latest
            if not sid:
                return
            if is_session_id_owned_by_others(sid, session_id, all_sessions):
                return
            if sid not in owned and sid not in discovered:
                discovered.append(sid)
            latest = sid

        for sid in session_ids_from_hook_state(session):
            consider(sid)

        if latest is None:
            cwd = getattr(session, "start_directory", None)
            if cwd:
                for path in self._find_by_cwd(str(cwd), since):
                    meta = _read_session_meta(path)
                    if meta:
                        consider(meta.get("id"))

        return DiscoveredSessionIds(ids=discovered, latest=latest)

    def get_window_token_usage(self, session: Any, since: datetime) -> Dict[str, int]:
        path = self._path_for(session)
        if path is None:
            return empty_window_usage()
        launch_dt = _launch_datetime(session)
        if launch_dt is not None and launch_dt < since:
            # `since` starts after this agent's own launch — only a slice of
            # its lifetime is in scope, but token_count events are running
            # totals with no reliable per-event timestamp to diff against,
            # so there is no safe way to isolate that slice. "Unknown" beats
            # mis-reporting the full-session total as the windowed one.
            return empty_window_usage()
        scan = _scan_rollout(path)
        usage = empty_window_usage()
        usage["input_tokens"] = scan["input_tokens"]
        usage["output_tokens"] = scan["output_tokens"]
        usage["cache_creation_tokens"] = scan["cache_write_tokens"]
        usage["cache_read_tokens"] = scan["cache_read_tokens"]
        return usage

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        # codex has no devcontainer story yet; the host filesystem is the
        # only source, and it is not visible from inside a container.
        return None


__all__ = [
    "CodexStatsReader",
    "codex_home",
    "schema_findings",
    "session_ids_from_hook_state",
    "sessions_root",
]
