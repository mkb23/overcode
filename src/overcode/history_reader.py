"""
Read Claude Code's history and session files for interaction/token counting.

Claude Code stores data in:
- ~/.claude/history.jsonl - interaction history (prompts sent)
- ~/.claude/projects/{encoded-path}/{sessionId}.jsonl - full conversation with token usage

Each assistant message in session files has usage data:
{
  "usage": {
    "input_tokens": 1003,
    "cache_creation_input_tokens": 2884,
    "cache_read_input_tokens": 25944,
    "output_tokens": 278
  }
}
"""

import json
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .session_manager import Session


CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"

# Claude Code encodes project dirs by dashing every non-alphanumeric char
_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"

# Model name → context window size in tokens.
# No default for unknown models (#469) — an unrecognized model renders a
# dash in the CTX column rather than being silently priced against some
# other model's window (the opencode bug report: a real 11,822-token/1%-used
# session showed 6% in overcode's TUI, because the unrecognized model
# ("gpt-5.6-sol", at the time not in this table) fell back to a 200K
# default that had nothing to do with the model actually in use —
# 11822/200000 ≈ 5.9% ≈ the observed 6%). Every entry below must be cited;
# add nothing you can't source. Claude Code with 1M context reports the
# same model ID as its 200K sibling — we detect the actual context size
# from token counts at runtime and update here for the models known to
# support extended context.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "claude-fable-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,

    # OpenAI / codex CLI models (#469). Sourced from this machine's own real
    # codex rollout JSONL (`~/.codex/sessions/**/rollout-*.jsonl`,
    # `payload.info.model_context_window`, verified 2026-08-28) — codex's own
    # CLI reports this figure at runtime, so these values are a direct
    # transcription of many real observed sessions, not a docs estimate.
    # `gpt-5-codex` is the one outlier (an older, pre-5.1 model, last seen
    # Nov 2025 on this machine) at a different window than the current 5.x
    # line. `CodexStatsReader` prefers the CLI-reported figure over this
    # table when available (see `AgentSessionStats.reported_context_window`)
    # — this table is codex's fallback/cross-check and opencode's only
    # source when it routes to one of these models.
    "gpt-5-codex": 272_000,
    "gpt-5.1-codex-max": 258_400,
    "gpt-5.1-codex-mini": 258_400,
    "gpt-5.4": 258_400,
    "gpt-5.6-sol": 258_400,
    "gpt-5.6-terra": 258_400,
    # gpt-4o family, reachable via opencode (not by codex, which only drives
    # the 5.x line). 128K per OpenAI's own model docs — these predate the
    # codex CLI's `model_context_window` reporting, so unlike the 5.x rows
    # above there is no live cross-check available for them.
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,

    # xAI Grok Build models (#469). Sourced from docs.x.ai/docs/models
    # (verified 2026-08-28) — the same source Phase 5's `pricing.py` entries
    # for these two models cite. Grok's own local session files
    # (`updates.jsonl`) do not report a context-window figure themselves
    # (only a running token count), so this static table is the only source
    # available for grok.
    "grok-4.6": 500_000,
    "grok-4.5": 500_000,

    # Zhipu GLM-4.6 (#469), reachable via opencode. Sourced from Z.AI's own
    # developer docs (docs.z.ai/guides/llm/glm-4.6, verified 2026-08-28):
    # "The context window has been expanded from 128K to 200K tokens."
    "glm-4.6": 200_000,

    # Moonshot Kimi K2 family (#469), reachable via opencode. Sourced from
    # platform.kimi.ai's own docs (verified 2026-08-28): kimi-k2.6, kimi-k2.5,
    # kimi-k2-0905-preview, kimi-k2-turbo-preview, kimi-k2-thinking, and
    # kimi-k2-thinking-turbo "all provide a 256K context window." The bare
    # hosted "kimi-k2" id the issue named is confirmed *discontinued* as of
    # 2026-05-25 per the same docs — deliberately not added here; a stale
    # session still reporting bare "kimi-k2" renders a dash rather than
    # borrowing a number from its replacement.
    "kimi-k2.6": 256_000,
    "kimi-k2.5": 256_000,
    "kimi-k2-0905-preview": 256_000,
    "kimi-k2-turbo-preview": 256_000,
    "kimi-k2-thinking": 256_000,
    "kimi-k2-thinking-turbo": 256_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000  # Retained for callers predating #469; no
                                   # longer used as an automatic fallback by
                                   # model_context_window() itself.

# Model ID → human-readable short name for display (MDL column).
#
# Style contract: every value is at most MODEL_SHORT_NAME_MAX_LEN chars and
# starts with a family tag (Fb/Op/Sn/Hk for Claude, G for GPT, Gk for Grok,
# GLM, K for Kimi) so mixed fleets read consistently. Ids not listed here
# fall back to the rule-based shortener below — this table is only for
# spellings the rules can't derive.
MODEL_SHORT_NAME_MAX_LEN = 7

MODEL_SHORT_NAMES: Dict[str, str] = {
    "claude-fable-5": "Fb5",
    "claude-opus-5": "Op5",
    "claude-sonnet-5": "Sn5",
    "claude-opus-4-8": "Op4.8",
    "claude-opus-4-7": "Op4.7",
    "claude-opus-4-6": "Op4.6",
    "claude-sonnet-4-6": "Sn4.6",
    "claude-sonnet-4-5-20250929": "Sn4.5",
    "claude-haiku-4-5-20251001": "Hk4.5",
    "claude-haiku-4-5": "Hk4.5",
    "claude-3-5-sonnet-20241022": "Sn3.5",
    "claude-3-5-haiku-20241022": "Hk3.5",
    "claude-3-opus-20240229": "Op3",
    "claude-3-sonnet-20240229": "Sn3",
    "claude-3-haiku-20240307": "Hk3",

    # OpenAI / codex (#469) — G + version + abbreviated variant.
    "gpt-5-codex": "G5Cdx",
    "gpt-5.1-codex-max": "G5.1Max",
    "gpt-5.1-codex-mini": "G5.1Mn",
    "gpt-5.4": "G5.4",
    "gpt-5.6-sol": "G5.6Sol",
    "gpt-5.6-terra": "G5.6Ter",
    "gpt-4o-mini": "G4oMn",
    "gpt-4o": "G4o",

    # xAI Grok (#469).
    "grok-4.6": "Gk4.6",
    "grok-4.5": "Gk4.5",

    # Zhipu GLM (#469).
    "glm-4.6": "GLM4.6",

    # Moonshot Kimi (#469).
    "kimi-k2.6": "K2.6",
    "kimi-k2.5": "K2.5",
    "kimi-k2-thinking": "K2Thk",
}

# Variant suffixes the rule-based shortener abbreviates ("-mini" → "Mn"…).
_VARIANT_ABBREVIATIONS: Dict[str, str] = {
    "mini": "Mn",
    "nano": "Nn",
    "max": "Max",
    "codex": "Cdx",
    "turbo": "T",
    "pro": "P",
    "flash": "F",
    "lite": "L",
    "thinking": "Thk",
    "instruct": "In",
    "chat": "",
    "latest": "",
    "preview": "",
}

_CLAUDE_FAMILY_TAGS: Dict[str, str] = {
    "fable": "Fb",
    "opus": "Op",
    "sonnet": "Sn",
    "haiku": "Hk",
}

_FAMILY_PREFIX_TAGS = [
    # (id prefix, display tag) — longest-match order.
    ("gpt-", "G"),
    ("grok-", "Gk"),
    ("glm-", "GLM"),
    ("gemini-", "Gm"),
    ("kimi-", "K"),
    ("deepseek-", "DS"),
    ("qwen", "Qw"),
    ("llama-", "Lm"),
    ("mistral-", "Ms"),
]

_DATE_SUFFIX_RE = re.compile(r"-20\d{6}$")
# Trailing capacity-variant suffix, e.g. the "[1m]" in "claude-opus-5[1m]".
_CAPACITY_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")


def _heuristic_short_name(bare: str) -> str:
    """Rule-based fallback for model ids not in MODEL_SHORT_NAMES.

    Aims for the same family-tag + version style as the table so an
    unfamiliar id degrades to something like ``Sn3.7`` or ``G4.1Mn``
    rather than a blunt prefix chop ("claude", "gpt-4o"). Never raises;
    the worst case is the cleaned id itself (renderers still truncate).
    """
    bare = _DATE_SUFFIX_RE.sub("", bare.strip().lower())
    if not bare:
        return ""

    # Claude ids carry family + digits in either order (claude-opus-4-8,
    # claude-3-7-sonnet). Find the family word, then join every digit
    # group with dots: Sn3.7, Op4.8, Fb5.
    if bare.startswith("claude"):
        for family, tag in _CLAUDE_FAMILY_TAGS.items():
            if family in bare:
                digits = re.findall(r"\d+", bare)
                return tag + ".".join(digits)
        return "Claude"

    # o-series reasoning models are already short (o3, o4-mini).
    if re.match(r"^o\d", bare):
        head, _, variant = bare.partition("-")
        return head + _VARIANT_ABBREVIATIONS.get(variant, variant.title())

    for prefix, tag in _FAMILY_PREFIX_TAGS:
        if bare.startswith(prefix):
            rest = bare[len(prefix):]
            parts = [p for p in rest.split("-") if p]
            out = tag
            for part in parts:
                if part in _VARIANT_ABBREVIATIONS:
                    out += _VARIANT_ABBREVIATIONS[part]
                elif re.fullmatch(r"[\d.]+", part):
                    out += part
                else:
                    out += part[:1].upper() + part[1:]
            return out

    # Unknown family: drop hyphens, title-case the chunks, let the
    # renderer truncate to its column width.
    return "".join(p[:1].upper() + p[1:] for p in bare.split("-") if p)


def _bare_model_id(model: str) -> str:
    """Normalise a model id to the bare form this module's tables key on.

    Strips an opencode-style ``provider/model`` qualifier and a trailing
    capacity-variant suffix in brackets, if present.

    opencode's stats reader stores the qualified id it launched with (e.g.
    ``"openai/gpt-5.6-sol"``, ``"anthropic/claude-opus-4-6"`` — see
    ``opencode_stats._parse_model``), while every other backend and this
    module's own lookup tables use the bare model id. Without this, EVERY
    opencode-launched model — including ones this table already knows,
    like Claude models routed through opencode — missed the table via an
    exact-match lookup on the qualified string and fell through to
    "unrecognized." This was very likely the underlying mechanism behind
    the #469 bug report, independent of which models the table happens to
    cover at any given time.

    The bracket suffix is a capacity variant, not a distinct model: Claude
    Code names its 1M-context Opus 5 ``claude-opus-5[1m]``. Left in place it
    breaks *both* lookups — an exact-match table miss (dash instead of a
    window) and, worse, a wrong short name, since the rule-based shortener
    joins every digit it finds and renders "Op5.1", which reads as a
    different model rather than as an unrecognized one.
    """
    bare = model.rsplit("/", 1)[-1] if "/" in model else model
    return _CAPACITY_SUFFIX_RE.sub("", bare)


def model_short_name(model: Optional[str]) -> str:
    """Return a short display name for a model ID.

    Examples:
        "claude-opus-4-6" → "Op4.6"
        "claude-haiku-4-5-20251001" → "Hk4.5"
        "openai/gpt-5.6-sol" (opencode-qualified) → "5.6Sol"
        "some-new-model" → "some-new-model" (unrecognized: shown verbatim,
            provider-qualifier stripped, not a dash — there's no wrong
            number to avoid here, just an unabbreviated name)
    """
    if not model:
        return ""
    bare = _bare_model_id(model)
    known = MODEL_SHORT_NAMES.get(bare)
    if known is not None:
        return known
    return _heuristic_short_name(bare) or bare


def model_context_window(model: Optional[str]) -> Optional[int]:
    """Return the context window size for a given model name.

    Returns None for unknown/None models (#469) — callers must render a
    dash, never assume some other model's window. Handles opencode's
    ``provider/model``-qualified ids the same way ``model_short_name`` does.
    """
    if not model:
        return None
    return MODEL_CONTEXT_WINDOWS.get(_bare_model_id(model))


def provider_from_model(model: Optional[str]) -> Optional[str]:
    """Derive API provider from a model ID returned in API responses.

    Older Bedrock model IDs have a dotted prefix (e.g. "us.anthropic.claude-..."),
    while API/Max IDs are plain (e.g. "claude-opus-4-7"). Note that current
    Bedrock responses often return the plain model ID too, so this heuristic
    only catches the dotted case — prefer provider_from_message_id when an
    assistant message ID is available.

    Returns "bedrock" for dotted IDs, "web" for plain, None if unknown/empty.
    """
    if not model:
        return None
    prefix = model.split("claude")[0] if "claude" in model else ""
    return "bedrock" if "." in prefix else "web"


def provider_from_message_id(msg_id: Optional[str]) -> Optional[str]:
    """Derive API provider from an assistant message ID.

    Bedrock responses stamp message IDs with a "msg_bdrk_" prefix; direct
    Anthropic API and Claude.ai OAuth use plain "msg_" IDs. This is more
    reliable than looking at the model field, which Bedrock now returns
    in its plain form (e.g. "claude-opus-4-6").

    Returns "bedrock", "web", or None if the ID doesn't match a known shape.
    """
    if not msg_id:
        return None
    if msg_id.startswith("msg_bdrk_"):
        return "bedrock"
    if msg_id.startswith("msg_"):
        return "web"
    return None


@dataclass
class AgentSessionStats:
    """Statistics for one agent session (tokens, context, work times)."""
    interaction_count: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    work_times: List[float]  # seconds per work cycle (prompt to next prompt)
    # Size of the most recent turn's prompt: uncached input + cache reads +
    # cache writes (all three are processed prompt — see read_session_file_stats).
    current_context_tokens: int = 0
    subagent_count: int = 0  # Number of subagent files (#176)
    live_subagent_count: int = 0  # Subagents with recently-modified files (#256)
    background_task_count: int = 0  # Number of background/farm tasks (#177)
    model: Optional[str] = None  # Most recently seen model name (#272)
    provider: Optional[str] = None  # Detected API provider ("web" or "bedrock")
    last_command: Optional[str] = None  # Most recent user prompt text
    # CLI-self-reported context window size, when a backend's own transcript
    # carries it (#469) — codex's rollout JSONL reports
    # `payload.info.model_context_window` per token_count event, a live
    # figure straight from the CLI rather than overcode's static table.
    # Preferred over `model_context_window(self.model)` in max_context_tokens
    # below when present. None for backends with no such signal (Claude,
    # grok, opencode), which fall through to the static table.
    reported_context_window: Optional[int] = None

    @property
    def max_context_tokens(self) -> Optional[int]:
        """Context window size for the detected model.

        None when neither the backend nor overcode's static table knows the
        model (#469) — callers must render a dash, never assume some other
        model's window (the original bug: an unrecognized model silently
        fell back to a 200K default with no relationship to the real model
        in use).
        """
        if self.reported_context_window:
            return self.reported_context_window
        return model_context_window(self.model)

    @property
    def total_tokens(self) -> int:
        """Total tokens (input + output, not counting cache)."""
        return self.input_tokens + self.output_tokens

    @property
    def total_tokens_with_cache(self) -> int:
        """Total tokens including cache operations."""
        return (self.input_tokens + self.output_tokens +
                self.cache_creation_tokens + self.cache_read_tokens)

    @property
    def median_work_time(self) -> float:
        """Median work time in seconds (50th percentile)."""
        if not self.work_times:
            return 0.0
        sorted_times = sorted(self.work_times)
        n = len(sorted_times)
        if n % 2 == 0:
            return (sorted_times[n // 2 - 1] + sorted_times[n // 2]) / 2
        return sorted_times[n // 2]


# Pre-backend name, kept for callers that still import it.
ClaudeSessionStats = AgentSessionStats


def synthesize_remote_stats(session) -> "AgentSessionStats":
    """Synthesize AgentSessionStats for a remote session from daemon_state.

    Remote sessions carry a remote_daemon_state dict with all
    SessionDaemonState fields. Extract what we need so that render
    columns (cost, tokens, context %, model) display correctly.
    """
    rds = getattr(session, 'remote_daemon_state', None) or {}
    stats = session.stats
    mwt = getattr(session, 'remote_median_work_time', None) or rds.get('median_work_time', 0.0)
    return AgentSessionStats(
        interaction_count=stats.interaction_count,
        input_tokens=rds.get('input_tokens', stats.total_tokens),
        output_tokens=rds.get('output_tokens', 0),
        cache_creation_tokens=rds.get('cache_creation_tokens', 0),
        cache_read_tokens=rds.get('cache_read_tokens', 0),
        work_times=[mwt] if mwt > 0 else [],
        current_context_tokens=rds.get('current_context_tokens', 0),
        model=rds.get('model'),
        last_command=rds.get('last_command'),
    )


@dataclass
class HistoryEntry:
    """A single interaction from Claude Code history."""
    display: str
    timestamp_ms: int
    project: Optional[str]
    session_id: Optional[str]

    @property
    def timestamp(self) -> datetime:
        """Convert millisecond timestamp to datetime."""
        return datetime.fromtimestamp(self.timestamp_ms / 1000)


class HistoryFile:
    """Cached reader for Claude Code's history.jsonl.

    All access to history.jsonl should go through this class.  It parses
    the file at most once per mtime+size change, so multiple callers in
    the same update cycle share a single parse.

    Thread-safe: a lock protects the cache so concurrent workers in a
    ThreadPoolExecutor can call methods without re-parsing.
    """

    def __init__(self, history_path: Path = CLAUDE_HISTORY_PATH):
        self._path = history_path
        self._lock = threading.Lock()
        self._cached_mtime: float = 0.0
        self._cached_size: int = 0
        self._cached_entries: List[HistoryEntry] = []
        # Separate cache for backward-read session ID lookups
        self._session_id_cache: Dict[str, Tuple[float, int, Optional[str]]] = {}

    # ── Core cache ────────────────────────────────────────────────────

    def _entries(self) -> List[HistoryEntry]:
        """Return parsed entries, re-parsing only if the file changed."""
        try:
            stat = self._path.stat()
        except OSError:
            return []

        with self._lock:
            if stat.st_mtime == self._cached_mtime and stat.st_size == self._cached_size:
                return self._cached_entries

            entries: List[HistoryEntry] = []
            try:
                with open(self._path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entries.append(HistoryEntry(
                                display=data.get("display", ""),
                                timestamp_ms=data.get("timestamp", 0),
                                project=data.get("project"),
                                session_id=data.get("sessionId"),
                            ))
                        except (json.JSONDecodeError, KeyError):
                            continue
            except IOError:
                return []

            self._cached_entries = entries
            self._cached_mtime = stat.st_mtime
            self._cached_size = stat.st_size
            return entries

    # ── Public query methods ──────────────────────────────────────────

    def read_all(self) -> List[HistoryEntry]:
        """Read all entries from history.jsonl (cached)."""
        return list(self._entries())

    def get_interactions_for_session(
        self, session: "Session"
    ) -> List[HistoryEntry]:
        """Get history entries matching a session's directory and time window.

        When the session has known agent_session_ids, filters by sessionId
        to avoid cross-contamination between agents sharing a directory (#264).
        Falls back to directory+timestamp matching for older sessions without
        tracked sessionIds.
        """
        if not session.start_directory:
            return []

        try:
            session_start = datetime.fromisoformat(session.start_time)
            session_start_ms = int(session_start.timestamp() * 1000)
        except (ValueError, TypeError):
            return []

        # Use owned sessionIds when available for precise matching (#264)
        owned_ids = set(getattr(session, 'agent_session_ids', None) or [])

        session_dir = str(Path(session.start_directory).resolve())
        matching = []

        for entry in self._entries():
            if entry.timestamp_ms < session_start_ms:
                continue
            if owned_ids:
                # Precise: only count interactions from this session's own Claude sessions
                if entry.session_id in owned_ids:
                    matching.append(entry)
            elif entry.project:
                # Fallback: directory matching for sessions without tracked IDs
                entry_dir = str(Path(entry.project).resolve())
                if entry_dir == session_dir:
                    matching.append(entry)

        return matching

    def count_interactions(self, session: "Session") -> int:
        """Count interactions for a session."""
        return len(self.get_interactions_for_session(session))

    def get_session_ids_for_session(self, session: "Session") -> List[str]:
        """Get unique Claude Code sessionIds for an overcode session."""
        entries = self.get_interactions_for_session(session)
        session_ids = set()
        for entry in entries:
            if entry.session_id:
                session_ids.add(entry.session_id)
        return sorted(session_ids)

    def get_current_session_id_for_directory(
        self, directory: str, since: datetime
    ) -> Optional[str]:
        """Get the most recent Claude sessionId for a directory.

        Optimized: reads history.jsonl backwards and caches by mtime+size.
        """
        if not self._path.exists():
            return None

        try:
            stat = self._path.stat()
            file_mtime = stat.st_mtime
            file_size = stat.st_size
        except OSError:
            return None

        session_dir = str(Path(directory).resolve())
        cache_key = session_dir

        with self._lock:
            cached = self._session_id_cache.get(cache_key)
            if cached and cached[0] == file_mtime and cached[1] == file_size:
                return cached[2]

        since_ms = int(since.timestamp() * 1000)

        result = None
        for line in _read_lines_reversed(self._path):
            try:
                data = json.loads(line)
                ts = data.get("timestamp", 0)
                if ts < since_ms:
                    break
                project = data.get("project")
                if project:
                    entry_dir = str(Path(project).resolve())
                    if entry_dir == session_dir:
                        sid = data.get("sessionId")
                        if sid:
                            result = sid
                            break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        with self._lock:
            self._session_id_cache[cache_key] = (file_mtime, file_size, result)
        return result


def _is_duplicate_subagent(subagent_file: Path) -> bool:
    """Detect subagent files that duplicate parent session messages.

    Claude Code's compaction (``/compact``, auto-compact) and side-question
    (``/btw``) features write conversation logs into subagent files named
    ``agent-acompact-*.jsonl`` or ``agent-aside_question-*.jsonl``.  When
    the first line has ``isMeta: true``, the file is a copy of messages
    already present in the parent session JSONL — counting its tokens
    would double-count spend.

    Small compact files (≤10 lines) without ``isMeta`` are the actual API
    calls Claude Code made to generate the compaction summary.  Those
    represent real, unique token usage and must still be counted.
    """
    name = subagent_file.name
    if not (name.startswith("agent-acompact-") or name.startswith("agent-aside_question-")):
        return False
    # Read only the first line to check isMeta — fast even for huge files
    try:
        with open(subagent_file, 'r') as f:
            first_line = f.readline().strip()
        if not first_line:
            return False
        data = json.loads(first_line)
        return bool(data.get("isMeta"))
    except (IOError, json.JSONDecodeError, TypeError):
        return False


def _read_lines_reversed(filepath: Path, max_bytes: int = 64 * 1024) -> List[str]:
    """Read the last chunk of a file and return lines in reverse order.

    Reads up to max_bytes from the end of the file. This is much faster than
    reading the entire file when we only need recent entries.
    """
    try:
        file_size = filepath.stat().st_size
    except OSError:
        return []

    read_size = min(file_size, max_bytes)
    try:
        with open(filepath, 'rb') as f:
            f.seek(max(0, file_size - read_size))
            chunk = f.read().decode('utf-8', errors='replace')
    except IOError:
        return []

    lines = chunk.split('\n')
    # First line may be partial if we didn't read from start — drop it
    if file_size > read_size and lines:
        lines = lines[1:]
    # Return non-empty lines in reverse order
    return [line for line in reversed(lines) if line.strip()]


# ── Module-level singleton for backward-compat free functions ─────────

_default_history = HistoryFile()


def read_history(history_path: Path = CLAUDE_HISTORY_PATH) -> List[HistoryEntry]:
    """Read all entries from history.jsonl.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.read_all()
    return HistoryFile(history_path).read_all()


def get_interactions_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[HistoryEntry]:
    """Get history entries matching a session.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_interactions_for_session(session)
    return HistoryFile(history_path).get_interactions_for_session(session)


def count_interactions(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> int:
    """Count interactions for a session."""
    return len(get_interactions_for_session(session, history_path))


def get_session_ids_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[str]:
    """Get unique Claude Code sessionIds for an overcode session."""
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_session_ids_for_session(session)
    return HistoryFile(history_path).get_session_ids_for_session(session)


def get_current_session_id_for_directory(
    directory: str,
    since: datetime,
    history_path: Path = CLAUDE_HISTORY_PATH
) -> Optional[str]:
    """Get the most recent Claude sessionId for a directory since a given time.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_current_session_id_for_directory(directory, since)
    return HistoryFile(history_path).get_current_session_id_for_directory(directory, since)


def encode_project_path(path: str) -> str:
    """Encode a project path to Claude Code's directory naming format.

    Claude Code stores project data in directories named like:
    /home/user/myproject   -> -home-user-myproject
    /home/user/.config     -> -home-user--config
    /home/user/my_app.dir  -> -home-user-my-app-dir

    Every non-alphanumeric character becomes '-' (verified empirically
    against Claude Code v2: underscores included — replacing only '/' and
    '.' broke token tracking for any project path containing '_').

    Args:
        path: The project path to encode

    Returns:
        Encoded directory name
    """
    resolved = str(Path(path).resolve())
    return _NON_ALNUM.sub("-", resolved)


def get_session_file_path(
    project_path: str,
    session_id: str,
    projects_path: Path = CLAUDE_PROJECTS_PATH
) -> Path:
    """Get the path to a Claude Code session JSONL file.

    Args:
        project_path: The project directory path
        session_id: The Claude Code sessionId
        projects_path: Base path for Claude projects

    Returns:
        Path to the session JSONL file
    """
    encoded = encode_project_path(project_path)
    return projects_path / encoded / f"{session_id}.jsonl"


def _parse_session_lines(
    lines,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Parse token usage and work times from session JSONL lines.

    Core parsing logic shared by read_session_file_stats (file-based)
    and read_session_stats_from_content (string-based, for containers).

    Args:
        lines: Iterable of JSONL line strings
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "current_context_tokens": 0,
        "model": None,
        "provider": None,
    }

    user_prompt_times: List[datetime] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            msg_type = data.get("type")

            if msg_type == "assistant":
                # Check timestamp if filtering by time
                if since:
                    ts_str = data.get("timestamp")
                    if ts_str:
                        try:
                            msg_time = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            ).astimezone().replace(tzinfo=None)
                            if msg_time < since:
                                continue
                        except (ValueError, TypeError):
                            pass

                message = data.get("message", {})
                usage = message.get("usage", {})
                if usage:
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_creation = usage.get(
                        "cache_creation_input_tokens", 0
                    )
                    totals["input_tokens"] += input_tokens
                    totals["output_tokens"] += output_tokens
                    totals["cache_creation_tokens"] += cache_creation
                    totals["cache_read_tokens"] += cache_read
                    # Every one of these three is prompt the model actually
                    # processed this turn, so all three count toward context
                    # occupancy. Omitting cache_creation under-reports badly
                    # rather than slightly: on a turn that *writes* the cache
                    # (the first turn after /clear, or after a cache expiry)
                    # the whole prompt lands in cache_creation and
                    # input_tokens is a literal handful — a 48K-token context
                    # reported as 2 tokens, rendering "0%" in a column whose
                    # entire job is to warn before the window fills.
                    context_size = input_tokens + cache_read + cache_creation
                    if context_size > 0:
                        totals["current_context_tokens"] = context_size
                    # Only track model/provider from messages with actual API
                    # usage (skips synthetic error messages with zero tokens).
                    if input_tokens + output_tokens + cache_creation + cache_read > 0:
                        model = message.get("model")
                        if model:
                            totals["model"] = model
                        detected = provider_from_message_id(message.get("id"))
                        if detected:
                            totals["provider"] = detected

            elif msg_type == "user":
                # Check if this is an actual user prompt (not a tool result)
                message = data.get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    if content and content[0].get("type") == "tool_result":
                        continue

                ts_str = data.get("timestamp")
                if not ts_str:
                    continue

                try:
                    msg_time = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).astimezone().replace(tzinfo=None)
                    if since and msg_time < since:
                        continue
                    user_prompt_times.append(msg_time)
                except (ValueError, TypeError):
                    continue

        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Calculate durations between consecutive prompts
    work_times = []
    for i in range(1, len(user_prompt_times)):
        duration = (user_prompt_times[i] - user_prompt_times[i - 1]).total_seconds()
        if duration > 0:
            work_times.append(duration)

    return totals, work_times


# Cache for read_session_file_stats: {path: (mtime, size, since, result)}.
# A long-lived agent accumulates many session files (100+ MB total), but only
# the active one changes between polls — without this cache _update_stats_async
# re-parsed every static file every 5s, pinning a CPU core. Keyed by path and
# invalidated on mtime/size/since change, so results stay identical to an
# uncached read. Mirrors the mtime+size caching HistoryFile already does.
_session_stats_cache: Dict[str, Tuple[float, int, Optional[datetime], Tuple[dict, List[float]]]] = {}
_session_stats_cache_lock = threading.Lock()
_SESSION_STATS_CACHE_MAX = 512


def read_session_file_stats(
    session_file: Path,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Read token usage and work times from a session file in a single pass.

    Combines the work of read_token_usage_from_session_file and
    read_work_times_from_session_file so the file is only read once.

    Results are cached per path and reused while the file's mtime and size (and
    the ``since`` filter) are unchanged, so a static multi-MB session file is
    parsed at most once rather than on every polling cycle.

    Args:
        session_file: Path to the session JSONL file
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    def _empty() -> Tuple[dict, List[float]]:
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "current_context_tokens": 0, "model": None, "provider": None,
        }, []

    try:
        stat = session_file.stat()
    except OSError:
        # Missing/unreadable file — matches the old exists() short-circuit.
        return _empty()

    key = str(session_file)
    with _session_stats_cache_lock:
        cached = _session_stats_cache.get(key)
        if (cached is not None
                and cached[0] == stat.st_mtime
                and cached[1] == stat.st_size
                and cached[2] == since):
            return cached[3]

    try:
        with open(session_file, 'r') as f:
            result = _parse_session_lines(f, since=since)
    except IOError:
        return _empty()

    with _session_stats_cache_lock:
        if len(_session_stats_cache) >= _SESSION_STATS_CACHE_MAX:
            # Bound growth: drop the oldest half (dicts preserve insertion order).
            for stale in list(_session_stats_cache)[:_SESSION_STATS_CACHE_MAX // 2]:
                del _session_stats_cache[stale]
        _session_stats_cache[key] = (stat.st_mtime, stat.st_size, since, result)

    return result


def read_session_stats_from_content(
    content: str,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Read token usage and work times from session JSONL content string.

    Same as read_session_file_stats but accepts string content instead
    of a file path.  Used for reading session files from containers
    via docker exec.

    Args:
        content: JSONL content as a string
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    if not content or not content.strip():
        defaults = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "current_context_tokens": 0, "model": None, "provider": None,
        }
        return defaults, []

    return _parse_session_lines(content.splitlines(), since=since)


def read_token_usage_from_session_file(
    session_file: Path,
    since: Optional[datetime] = None
) -> dict:
    """Read token usage from a Claude Code session JSONL file.

    Args:
        session_file: Path to the session JSONL file
        since: Only count tokens from messages after this time

    Returns:
        Dict with input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
        and current_context_tokens (most recent turn's full prompt size)
    """
    totals, _ = read_session_file_stats(session_file, since)
    return totals


def read_work_times_from_session_file(
    session_file: Path,
    since: Optional[datetime] = None
) -> List[float]:
    """Calculate work times from a Claude Code session file.

    Work time = time from one user prompt to the next user prompt.
    This represents how long the agent worked autonomously.

    Only counts actual user prompts (not tool results which are automatic).

    Args:
        session_file: Path to the session JSONL file
        since: Only count work times from messages after this time

    Returns:
        List of work times in seconds
    """
    _, work_times = read_session_file_stats(session_file, since)
    return work_times


def get_session_stats(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH,
    projects_path: Path = CLAUDE_PROJECTS_PATH,
    history_file: Optional["HistoryFile"] = None,
) -> Optional[AgentSessionStats]:
    """Get comprehensive stats for an overcode session.

    Combines interaction counting with token usage from session files.

    Session scoping: get_interactions_for_session() is the single source of
    truth for which Claude Code sessions belong to this overcode session.
    When agent_session_ids are tracked, it filters precisely by sessionId;
    otherwise falls back to directory+timestamp matching (#119, #264).

    Context window uses active_agent_session_id after /clear (#116),
    falling back to MAX across all matched sessions.

    Args:
        session: The overcode Session
        history_path: Path to history.jsonl
        projects_path: Path to Claude projects directory
        history_file: Optional HistoryFile for cached access (avoids re-parsing)

    Returns:
        AgentSessionStats if session has start_directory, None otherwise
    """
    if not session.start_directory:
        return None

    # Parse session start time for filtering.
    # session.start_time is local time (naive), but Claude Code session files
    # store timestamps in UTC.  Convert to UTC-naive for correct comparison.
    try:
        session_start_local = datetime.fromisoformat(session.start_time)
        session_start = session_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None

    # get_interactions_for_session is the single gate for session scoping:
    # uses agent_session_ids when available, else directory+timestamp fallback
    hf = history_file or (
        _default_history if history_path == CLAUDE_HISTORY_PATH
        else HistoryFile(history_path)
    )
    interactions = hf.get_interactions_for_session(session)
    interaction_count = len(interactions)

    # Derive Claude sessionIds and their project paths from interactions.
    # Claude Code may store session files under a different project path
    # than start_directory (e.g., when the directory doesn't exist or Claude
    # chooses a different project root).
    session_ids = {e.session_id for e in interactions if e.session_id}
    sid_to_project: Dict[str, str] = {}
    for e in interactions:
        if e.session_id and e.project:
            sid_to_project[e.session_id] = e.project

    # Active session ID for context window after /clear (#116)
    active_session_id = getattr(session, 'active_agent_session_id', None)

    # Sum token usage and work times across session files
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    current_context = 0
    detected_model: Optional[str] = None
    detected_provider: Optional[str] = None
    all_work_times: List[float] = []
    subagent_count = 0  # Count subagent files (#176)
    live_subagent_count = 0  # Subagents with recently-modified files (#256)
    background_task_count = 0  # Count background task files (#177)
    now = time.time()

    for sid in session_ids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        # Fall back to the project path from history entries if the session
        # file doesn't exist at the expected start_directory path.  Claude
        # Code may use a different project root (e.g. home dir) when the
        # launch directory no longer exists.
        if not session_file.exists():
            alt_project = sid_to_project.get(sid)
            if alt_project:
                session_file = get_session_file_path(
                    alt_project, sid, projects_path
                )
        usage, work_times = read_session_file_stats(session_file, since=session_start)
        total_input += usage["input_tokens"]
        total_output += usage["output_tokens"]
        total_cache_creation += usage["cache_creation_tokens"]
        total_cache_read += usage["cache_read_tokens"]

        # Context & model: prefer active session (#116), fall back to MAX across all
        if active_session_id:
            if sid == active_session_id:
                current_context = usage["current_context_tokens"]
                if usage["model"]:
                    detected_model = usage["model"]
                if usage["provider"]:
                    detected_provider = usage["provider"]
        else:
            if usage["current_context_tokens"] > current_context:
                current_context = usage["current_context_tokens"]
            if usage["model"]:
                detected_model = usage["model"]
            if usage["provider"]:
                detected_provider = usage["provider"]

        # Collect work times from this session file
        all_work_times.extend(work_times)

        # Check for subagent files in {sessionId}/subagents/
        # Use the actual project path where the session file was found.
        actual_project = sid_to_project.get(sid, session.start_directory)
        encoded = encode_project_path(actual_project)
        subagents_dir = projects_path / encoded / sid / "subagents"
        if subagents_dir.exists():
            for subagent_file in subagents_dir.glob("agent-*.jsonl"):
                # Skip duplicate conversation logs from compaction/side-question
                # subagents. Claude Code writes these with isMeta=True and they
                # contain copies of messages already in the parent session file.
                # See docs/claude-session-files.md for details.
                if _is_duplicate_subagent(subagent_file):
                    continue
                subagent_count += 1
                if now - subagent_file.stat().st_mtime < 30:
                    live_subagent_count += 1
                sub_usage, _ = read_session_file_stats(
                    subagent_file, since=session_start
                )
                total_input += sub_usage["input_tokens"]
                total_output += sub_usage["output_tokens"]
                total_cache_creation += sub_usage["cache_creation_tokens"]
                total_cache_read += sub_usage["cache_read_tokens"]

        # Check for background tasks (run_in_background agents) (#177)
        # These are subagents that were started in background mode
        tasks_dir = projects_path / encoded / sid / "tasks"
        if tasks_dir.exists():
            background_task_count += len(list(tasks_dir.glob("task-*.jsonl")))

    # Extract last command from history interactions
    last_command = None
    if interactions:
        last_entry = interactions[-1]
        if last_entry.display:
            last_command = last_entry.display

    return AgentSessionStats(
        interaction_count=interaction_count,
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_tokens=total_cache_creation,
        cache_read_tokens=total_cache_read,
        work_times=all_work_times,
        current_context_tokens=current_context,
        subagent_count=subagent_count,
        live_subagent_count=live_subagent_count,
        background_task_count=background_task_count,
        model=detected_model,
        provider=detected_provider,
        last_command=last_command,
    )


def read_window_token_usage(
    session_file: Path,
    since: datetime,
) -> dict:
    """Sum token usage for assistant messages timestamped at or after ``since``.

    Lighter than read_session_file_stats — only walks the JSONL once tracking
    a single set of totals, skipping work-time / model / provider extraction.
    Used by the burn-rate calculation, which re-parses files independently of
    the daemon's full stats sync (#174).

    ``since`` should be a LOCAL-naive datetime. Each message's UTC timestamp
    is converted to the local zone and stripped of tzinfo before comparison,
    matching the convention in _parse_session_lines.

    Returns dict with input_tokens, output_tokens, cache_creation_tokens,
    cache_read_tokens (all zero if the file is missing or unreadable).
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    if not session_file.exists():
        return totals

    try:
        with open(session_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") != "assistant":
                        continue
                    ts_str = data.get("timestamp")
                    if not ts_str:
                        continue
                    msg_time = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).astimezone().replace(tzinfo=None)
                    if msg_time < since:
                        continue
                    usage = data.get("message", {}).get("usage") or {}
                    totals["input_tokens"] += usage.get("input_tokens", 0)
                    totals["output_tokens"] += usage.get("output_tokens", 0)
                    totals["cache_read_tokens"] += usage.get(
                        "cache_read_input_tokens", 0
                    )
                    totals["cache_creation_tokens"] += usage.get(
                        "cache_creation_input_tokens", 0
                    )
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except IOError:
        pass

    return totals


def get_session_window_token_usage(
    session: "Session",
    since: datetime,
    projects_path: Optional[Path] = None,
) -> dict:
    """Sum window-scoped tokens across a session's primary + subagent files.

    Mirrors the file discovery in get_session_stats so subagent token spend
    (parallel workflows) is counted alongside the main conversation.

    Returns dict with input_tokens, output_tokens, cache_creation_tokens,
    cache_read_tokens — totals over messages timestamped at or after ``since``.
    """
    # Resolve the projects path at call time so monkeypatching
    # CLAUDE_PROJECTS_PATH (in tests) takes effect without each caller
    # having to thread it through.
    if projects_path is None:
        projects_path = CLAUDE_PROJECTS_PATH

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    if not session.start_directory:
        return totals

    sids = list(getattr(session, 'agent_session_ids', None) or [])
    if not sids:
        return totals

    for sid in sids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        if not session_file.exists():
            continue
        u = read_window_token_usage(session_file, since)
        for k in totals:
            totals[k] += u[k]

        # Include subagent files (parallel workflows), skipping duplicate
        # compaction/side-question logs that copy parent messages.
        encoded = encode_project_path(session.start_directory)
        subagents_dir = projects_path / encoded / sid / "subagents"
        if subagents_dir.exists():
            for sub_file in subagents_dir.glob("agent-*.jsonl"):
                if _is_duplicate_subagent(sub_file):
                    continue
                u = read_window_token_usage(sub_file, since)
                for k in totals:
                    totals[k] += u[k]

    return totals
