"""grok stats: the ``updates.jsonl``/``summary.json``/``prompt_history.jsonl``
reader behind ``StatsReader``.

Phase 4 of ``docs/design/agent-backends-codex-grok.md`` (§3.4, Appendix B).
Unlike codex's single append-only rollout file, grok scatters what overcode
needs across a few files inside one per-session directory:

    ~/.grok/sessions/<percent-encoded-abs-cwd>/<session-uuid>/
        updates.jsonl        persisted ACP session/update stream
        summary.json         current_model_id, num_messages, git info
    ~/.grok/sessions/<percent-encoded-abs-cwd>/
        prompt_history.jsonl per-project prompt log, one line per turn,
                              tagged with the owning session id

Two things §3.4 flagged as unconfirmed were determined empirically against a
real 413-message session (``01a015cb-...`` under the xway project dir) before
this reader was written, and are recorded here rather than left as a Phase 4
guess:

1. **Per-turn, not cumulative.** ``turn_completed.usage`` objects in a real
   multi-turn ``updates.jsonl`` are NOT monotonically increasing across the
   file (observed sequence of ``inputTokens``: 4,130,868 -> 89,480 -> 452,829
   -> ...) — each one covers only the turns since the previous report
   (``numTurns``/``modelCalls`` vary the same way). The reader therefore
   SUMS every ``turn_completed.usage`` in the file rather than taking "the
   latest one" the way codex's cumulative ``token_count`` events are read.
2. **``costUsdTicks`` is nano-dollars** (1e9 ticks per USD), not the
   millionths the design doc's single sample was consistent with but hadn't
   ruled out. Cross-checked against the same real session: a batch of
   ~13,600 non-cached input tokens + heavy reasoning effort priced at
   113,440,000 ticks -> $0.11344, and a larger batch (791k uncached input +
   3.34M cached input + 137k output/reasoning tokens) priced at
   7,295,125,400 ticks -> $7.30 — both land in the right dollar-per-token
   ballpark for grok's published pricing; the 1e6 (millionths) reading would
   have put the second turn at $7,295, which is not plausible for the token
   counts involved.

``_meta.totalTokens`` is the running context-size proxy the design doc
described; the latest one seen in file order is "current context". It lives
at ``params._meta.totalTokens`` — nested inside ``params`` alongside
``update``, not at the envelope's top level as an early reading of the same
real session suggested (confirmed by grepping the raw file: zero top-level
``_meta`` keys, 333 ``params._meta.totalTokens`` occurrences).

Same defensive posture as ``CodexStatsReader``/``OpencodeStatsReader``
throughout: read-only, any surprise degrades to None/empty rather than
raising into a daemon tick, and a shape that doesn't match what this reader
expects is a ``schema_findings()`` doctor warning, not a crash.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from ..stats_reader import (
    AgentSessionStats,
    DiscoveredSessionIds,
    empty_window_usage,
)
from .grok import grok_home

# costUsdTicks unit, empirically determined (see module docstring point 2):
# nano-dollars, i.e. 1e9 ticks == $1.00.
_COST_TICKS_PER_USD = 1_000_000_000

# Keys expected inside a turn_completed update's usage object. Absence of
# all of these from the newest session's newest turn_completed event is
# schema drift, not "grok hasn't produced one yet" (see schema_findings()).
_EXPECTED_USAGE_KEYS: tuple = (
    "inputTokens",
    "outputTokens",
    "cachedReadTokens",
    "reasoningTokens",
    "costUsdTicks",
    "totalTokens",
)


def sessions_root() -> Path:
    """Where grok writes per-project session directories."""
    return grok_home() / "sessions"


def encode_cwd(cwd: str) -> str:
    """grok's project-directory encoding: the full absolute path,
    percent-encoded including the leading slash (Appendix B of the design
    doc, round-trip confirmed live: ``/`` -> ``%2F``). ``quote(..., safe="")``
    reproduces this exactly — the unreserved characters (letters, digits,
    ``_.-~``) it always leaves alone are exactly the ones grok's own encoding
    leaves alone (e.g. ``.claude`` stays ``.claude``, never ``%2Eclaude``).
    """
    return quote(cwd, safe="")


def project_dir(cwd: str, root: Optional[Path] = None) -> Path:
    """The per-project directory holding this cwd's sessions + prompt log."""
    base = root if root is not None else sessions_root()
    return base / encode_cwd(cwd)


def session_dir(cwd: str, session_id: str, root: Optional[Path] = None) -> Path:
    """The one directory a specific grok session's files live in."""
    return project_dir(cwd, root) / session_id


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


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _turn_completed_usage(entry: Any) -> Optional[dict]:
    """The ``usage`` object of a ``turn_completed`` update line, or None."""
    if not isinstance(entry, dict):
        return None
    params = entry.get("params")
    update = params.get("update") if isinstance(params, dict) else None
    if not isinstance(update, dict) or update.get("sessionUpdate") != "turn_completed":
        return None
    usage = update.get("usage")
    return usage if isinstance(usage, dict) else None


def _scan_updates(path: Path, *, since_ts: Optional[float] = None) -> Dict[str, Any]:
    """One pass over an ``updates.jsonl`` file for the fields the columns need.

    ``turn_completed.usage`` objects are per-turn batches, not a running
    cumulative total (module docstring point 1) — summed across every one in
    the file (or, when ``since_ts`` is given, every one at/after it).
    ``_meta.totalTokens`` IS a running total, so the latest one seen in file
    order (unfiltered by ``since_ts`` — there's no reliable way to isolate a
    windowed context size) is "current context".
    """
    out: Dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "current_context_tokens": 0,
    }
    for entry in _iter_jsonl(path):
        if not isinstance(entry, dict):
            continue

        # `_meta` sits inside `params`, not at the envelope's top level —
        # confirmed against the real xway session file (413-message,
        # module docstring), which has zero top-level `_meta` keys.
        params = entry.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        if isinstance(meta, dict) and "totalTokens" in meta:
            out["current_context_tokens"] = _as_int(meta.get("totalTokens"))

        usage = _turn_completed_usage(entry)
        if usage is None:
            continue
        if since_ts is not None and _as_number(entry.get("timestamp")) < since_ts:
            continue
        out["input_tokens"] += _as_int(usage.get("inputTokens"))
        # reasoning has no bucket of its own, so it folds into output rather
        # than vanishing from the totals — matches the codex/opencode
        # convention this reader follows elsewhere.
        out["output_tokens"] += _as_int(usage.get("outputTokens")) + _as_int(
            usage.get("reasoningTokens")
        )
        out["cache_read_tokens"] += _as_int(usage.get("cachedReadTokens"))
        out["cache_creation_tokens"] += _as_int(usage.get("cacheCreationTokens"))
        ticks = usage.get("costUsdTicks")
        if isinstance(ticks, (int, float)):
            out["cost_usd"] += ticks / _COST_TICKS_PER_USD
    return out


def _read_model(session_dir_path: Path) -> Optional[str]:
    try:
        summary = json.loads((session_dir_path / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(summary, dict):
        return None
    model = summary.get("current_model_id")
    return model if isinstance(model, str) and model else None


def _count_prompts(project_dir_path: Path, session_id: str) -> int:
    path = project_dir_path / "prompt_history.jsonl"
    count = 0
    for entry in _iter_jsonl(path):
        if isinstance(entry, dict) and entry.get("session_id") == session_id:
            count += 1
    return count


def schema_findings() -> List[str]:
    """Doctor warning when the newest session's usage shape has drifted.

    Empty when there is nothing to check (no sessions yet — not a fault) or
    the shape matches. Best-effort: only the most recently modified session
    directory across the whole sessions root is inspected, since a full scan
    of every session ever recorded would be far too expensive for a doctor
    pass.
    """
    root = sessions_root()
    try:
        session_dirs = [
            p for p in root.glob("*/*") if p.is_dir() and (p / "updates.jsonl").exists()
        ]
    except OSError:
        return []
    if not session_dirs:
        return []
    try:
        latest = max(session_dirs, key=lambda p: (p / "updates.jsonl").stat().st_mtime)
    except OSError:
        return []

    usage_keys: Optional[set] = None
    for entry in _iter_jsonl(latest / "updates.jsonl"):
        usage = _turn_completed_usage(entry)
        if usage is not None:
            usage_keys = set(usage.keys())
    if not usage_keys:
        # No turn_completed event seen yet in this file — likely just an
        # early session, not drift.
        return []
    missing = [key for key in _EXPECTED_USAGE_KEYS if key not in usage_keys]
    if not missing:
        return []
    return [
        "grok's updates.jsonl turn_completed.usage shape has drifted — missing "
        + ", ".join(missing)
        + f" in {latest / 'updates.jsonl'}. Token/cost/context columns will show "
        "dashes until overcode is updated."
    ]


class GrokStatsReader:
    """Reads grok's per-session files for one agent.

    Unlike codex/opencode, session location needs no discovery: grok's
    SESSION_ID_PRESCRIPTION means overcode always knows the session id up
    front (it minted it), so this reader keys straight into
    ``sessions/<enc-cwd>/<uuid>/`` (design doc §3.4). Any failure — missing
    directory, a corrupt line, an unreadable file — answers "unknown" so the
    columns render dashes instead of zeros.
    """

    backend_name = "grok"

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

    def _dir_for(self, session: Any, session_id: str) -> Optional[Path]:
        cwd = getattr(session, "start_directory", None)
        if not cwd or not session_id:
            return None
        candidate = session_dir(str(cwd), session_id, root=self._root())
        return candidate if candidate.exists() else None

    def _resolve(self, session: Any) -> Optional[Path]:
        for session_id in reversed(self._owned_ids(session)):
            found = self._dir_for(session, session_id)
            if found is not None:
                return found
        return None

    def _project_dir(self, session: Any) -> Optional[Path]:
        cwd = getattr(session, "start_directory", None)
        if not cwd:
            return None
        return project_dir(str(cwd), root=self._root())

    # -- StatsReader -------------------------------------------------------

    def get_stats(self, session: Any, *, history_file: Any = None) -> Optional[AgentSessionStats]:
        session_path = self._resolve(session)
        if session_path is None:
            return None
        scan = _scan_updates(session_path / "updates.jsonl")
        model = _read_model(session_path)
        proj = self._project_dir(session)
        session_id = session_path.name
        interaction_count = _count_prompts(proj, session_id) if proj is not None else 0

        if not model and interaction_count == 0 and not any((
            scan["input_tokens"], scan["output_tokens"], scan["cache_read_tokens"],
            scan["cache_creation_tokens"], scan["current_context_tokens"],
        )):
            # Nothing usable was actually read — reads as unknown, not an
            # all-zero session (covers both an empty file and a drifted
            # turn_completed whose usage object carried none of the keys
            # this reader knows about).
            return None

        return AgentSessionStats(
            interaction_count=interaction_count,
            input_tokens=scan["input_tokens"],
            output_tokens=scan["output_tokens"],
            cache_creation_tokens=scan["cache_creation_tokens"],
            cache_read_tokens=scan["cache_read_tokens"],
            work_times=[],
            current_context_tokens=scan["current_context_tokens"],
            model=model,
            # Deliberately None — see OpencodeStatsReader's identical
            # comment: `provider` is overcode's API-transport discriminator,
            # not the model's vendor.
            provider=None,
        )

    def get_stored_cost(self, session: Any) -> Optional[float]:
        """grok's own ``costUsdTicks`` total for this agent, or None.

        Preferred over recomputing from tokens because grok records the
        actual billed amount (module docstring point 2 for the ticks->USD
        conversion). Returns None when zero so the caller falls back to
        ``pricing.py``, same posture as ``OpencodeStatsReader``.
        """
        session_path = self._resolve(session)
        if session_path is None:
            return None
        scan = _scan_updates(session_path / "updates.jsonl")
        cost = scan["cost_usd"]
        return cost if cost > 0 else None

    def get_current_session_id(self, session: Any, since: datetime) -> Optional[str]:
        owned = self._owned_ids(session)
        return owned[-1] if owned else None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        # SESSION_ID_PRESCRIPTION means overcode always minted this agent's
        # session id itself — there is nothing to discover on disk that the
        # session object doesn't already know, unlike codex/opencode which
        # have to find an id they never chose.
        owned = self._owned_ids(session)
        return DiscoveredSessionIds(ids=[], latest=owned[-1] if owned else None)

    def get_window_token_usage(self, session: Any, since: datetime) -> Dict[str, int]:
        session_path = self._resolve(session)
        if session_path is None:
            return empty_window_usage()
        scan = _scan_updates(session_path / "updates.jsonl", since_ts=since.timestamp())
        usage = empty_window_usage()
        usage["input_tokens"] = scan["input_tokens"]
        usage["output_tokens"] = scan["output_tokens"]
        usage["cache_creation_tokens"] = scan["cache_creation_tokens"]
        usage["cache_read_tokens"] = scan["cache_read_tokens"]
        return usage

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        # grok has no devcontainer story yet; the host filesystem is the
        # only source, and it is not visible from inside a container.
        return None


__all__ = [
    "GrokStatsReader",
    "encode_cwd",
    "project_dir",
    "schema_findings",
    "session_dir",
    "sessions_root",
]
