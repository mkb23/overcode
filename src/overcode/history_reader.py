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

import bisect
import json
import os
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING
from dataclasses import dataclass

from . import conversation_spend, model_metadata
from .stat_gate import FileSignature, is_settled, stat_signature
from .transcript_index import (
    COPY_WINDOW_SECONDS,
    LONG_KEYS,
    TranscriptRegistry,
    cache_write_1h,
    datetime_key,
    empty_stats,
    empty_window,
    is_long_request,
    key_before,
    parse_message_time,
)

if TYPE_CHECKING:
    from .session_manager import Session


CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"

# Claude Code encodes project dirs by dashing every non-alphanumeric char
_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"

# Path.resolve() results by the string resolved. A resolve is an lstat per
# path component (~25 us); the same few dozen project strings are resolved
# thousands of times a tick (once per owned id in encode_project_path, once
# per history entry in the directory-matching fallbacks, audit R8), so the
# memo turns those into dict lookups. A memoised answer goes stale only if
# a symlink inside a project path is re-pointed while overcode runs.
_resolved_paths: Dict[str, str] = {}
_resolved_paths_lock = threading.Lock()
_RESOLVED_PATHS_MAX = 8192


def resolve_project_path(path: str) -> str:
    """``str(Path(path).resolve())``, memoised per distinct ``path`` string."""
    with _resolved_paths_lock:
        hit = _resolved_paths.get(path)
    if hit is not None:
        return hit
    resolved = str(Path(path).resolve())
    with _resolved_paths_lock:
        if len(_resolved_paths) >= _RESOLVED_PATHS_MAX:
            _resolved_paths.clear()
        _resolved_paths[path] = resolved
    return resolved

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
#
# This table is the *curated* tier: a hand-verified primary source or a live
# CLI figure per row. Ids missing here fall through to the bundled
# models.dev snapshot (`model_metadata.py`, #473) before rendering a dash —
# see model_context_window() below. Curated always wins where both know a
# model, so the codex-reported 258,400 for the gpt-5.6 line stays put even
# though models.dev advertises that family's 1.05M long-context ceiling.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # Claude: platform.claude.com model overview (cached in the claude-api
    # skill, 2026-06-24). Claude Code reports the dash-versioned ids
    # (claude-opus-5-5); a point release missing here still resolves to its
    # family's row through claude_family_id() below (#519).
    "claude-fable-5-1": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-opus-5-5": 1_000_000,
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

# Model ID → human-readable short name for display (MDL column).
#
# Style contract: every value is at most MODEL_SHORT_NAME_MAX_LEN chars and
# starts with a family tag (Fb/Op/Sn/Hk for Claude, G for GPT, Gk for Grok,
# GLM, K for Kimi) so mixed fleets read consistently. Ids not listed here
# fall back to the rule-based shortener below — this table is only for
# spellings the rules can't derive.
MODEL_SHORT_NAME_MAX_LEN = 7

MODEL_SHORT_NAMES: Dict[str, str] = {
    "claude-fable-5-1": "Fb5.1",
    "claude-fable-5": "Fb5",
    "claude-opus-5-5": "Op5.5",
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

# A Claude id as family + major version, then any point-release and date
# segments: claude-opus-5-5, claude-fable-5-1, claude-opus-5-5-20261001.
_CLAUDE_VERSIONED_ID_RE = re.compile(
    r"^(claude-(?:fable|opus|sonnet|haiku|mythos)-\d+)(?:-\d+)*?(?:-20\d{6})?$"
)


def claude_family_id(bare: str) -> Optional[str]:
    """The family + major version a Claude point release belongs to, or None.

    ``claude-opus-5-5`` → ``claude-opus-5``; ``claude-fable-5-1-20261001`` →
    ``claude-fable-5``. The major version is never dropped, so an unknown
    generation (``claude-opus-6-1`` → ``claude-opus-6``) only resolves if
    that generation itself is curated — a guess across generations could
    be off by 5x (200K vs 1M). None when the id is already a family id or
    not a dash-versioned Claude id.
    """
    match = _CLAUDE_VERSIONED_ID_RE.match(bare.lower())
    if not match:
        return None
    family = match.group(1)
    return family if family != bare.lower() else None


def curated_context_window(model: Optional[str]) -> Optional[Tuple[str, int]]:
    """``(table key, window)`` from the curated table, or None.

    Exact bare id first, then the Claude family the id is a point release
    of (#519: Claude Code reported ``claude-opus-5-5`` while the table only
    had ``claude-opus-5``, so CTX% rendered a dash).
    """
    if not model:
        return None
    bare = _bare_model_id(model)
    known = MODEL_CONTEXT_WINDOWS.get(bare)
    if known is not None:
        return bare, known
    family = claude_family_id(bare)
    if family is not None and family in MODEL_CONTEXT_WINDOWS:
        return family, MODEL_CONTEXT_WINDOWS[family]
    return None


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
    return model_metadata.bare_model_id(model)


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

    Resolution order (#473, #519): the curated table above by exact id,
    then by Claude family (a point release such as ``claude-opus-5-5``
    inherits ``claude-opus-5``'s window, never a different generation's),
    then the bundled models.dev snapshot (``model_metadata``), then None.
    """
    if not model:
        return None
    curated = curated_context_window(model)
    if curated is not None:
        return curated[1]
    return model_metadata.context_window(_bare_model_id(model))


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
    # Most recently seen reasoning effort, verbatim from the backend's own
    # store ("low"/"medium"/"high"/"xhigh"/"max"/… — vocabularies differ
    # per harness) (#497)
    effort: Optional[str] = None
    agent: Optional[str] = None  # detected agent persona, opencode2's NULL-row fallback
    pr_number: Optional[int] = None  # PR the session is linked to (Claude pr-link, #489)
    last_command: Optional[str] = None  # Most recent user prompt text
    # The backend's *own* context window for this session, when it has one
    # (#469) — codex's rollout JSONL reports `payload.info.model_context_window`
    # per token_count event (a live figure straight from the CLI), and
    # opencode's is the `limit.context` of its own models.dev catalog, which
    # is the exact denominator its console's "N% used" divides by (see
    # `opencode_stats.opencode_context_limit`). Preferred over
    # `model_context_window(self.model)` in max_context_tokens below when
    # present, so the CTX column agrees with what the agent's own UI shows.
    # None for backends with no such signal (Claude, grok), which fall
    # through to the static tables.
    reported_context_window: Optional[int] = None
    # The read split by the agent CLI's own conversation id
    # (``conversation_spend.usage`` per id): what the daemon keeps spend
    # per conversation by. None from a reader that cannot split it; its
    # totals are then one pseudo conversation.
    per_conversation: Optional[Dict[str, Dict[str, Any]]] = None

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
        effort=rds.get('effort'),
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

    Each parse also indexes entry positions by sessionId and by raw project
    string, so a per-session lookup costs its matches rather than a scan of
    every entry (audit R8: the 5 s sweep did one full scan per widget).

    Thread-safe: a lock protects the cache so concurrent workers in a
    ThreadPoolExecutor can call methods without re-parsing.
    """

    def __init__(self, history_path: Path = CLAUDE_HISTORY_PATH):
        self._path = history_path
        self._lock = threading.Lock()
        self._cached_mtime: float = 0.0
        self._cached_size: int = 0
        # (entries, positions by sessionId, positions by raw project string),
        # replaced as one object so a reader never pairs an index with the
        # entries of a different parse.
        self._parsed: Tuple[List[HistoryEntry], Dict[str, List[int]], Dict[str, List[int]]] = (
            [], {}, {}
        )
        # Separate cache for backward-read session ID lookups
        self._session_id_cache: Dict[str, Tuple[float, int, Optional[str]]] = {}

    # ── Core cache ────────────────────────────────────────────────────

    def _entries(self) -> List[HistoryEntry]:
        """Return parsed entries, re-parsing only if the file changed."""
        return self._snapshot()[0]

    def _snapshot(self) -> Tuple[List[HistoryEntry], Dict[str, List[int]], Dict[str, List[int]]]:
        """Parsed entries with their indexes, re-parsing only if the file changed."""
        try:
            stat = self._path.stat()
        except OSError:
            return [], {}, {}

        with self._lock:
            if stat.st_mtime == self._cached_mtime and stat.st_size == self._cached_size:
                return self._parsed

            entries: List[HistoryEntry] = []
            by_sid: Dict[str, List[int]] = {}
            by_project: Dict[str, List[int]] = {}
            try:
                with open(self._path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entry = HistoryEntry(
                                display=data.get("display", ""),
                                timestamp_ms=data.get("timestamp", 0),
                                project=data.get("project"),
                                session_id=data.get("sessionId"),
                            )
                        except (json.JSONDecodeError, KeyError):
                            continue
                        position = len(entries)
                        entries.append(entry)
                        if entry.session_id is not None:
                            by_sid.setdefault(entry.session_id, []).append(position)
                        if entry.project:
                            by_project.setdefault(entry.project, []).append(position)
            except IOError:
                return [], {}, {}

            self._parsed = (entries, by_sid, by_project)
            self._cached_mtime = stat.st_mtime
            self._cached_size = stat.st_size
            return self._parsed

    # ── Public query methods ──────────────────────────────────────────

    def read_all(self) -> List[HistoryEntry]:
        """Read all entries from history.jsonl (cached)."""
        return list(self._entries())

    def iter_entries(self) -> List[HistoryEntry]:
        """The cached entries themselves, without the copy ``read_all`` makes.

        Read-only: callers iterate and must not mutate the list.
        """
        return self._entries()

    def signature(self) -> Optional[FileSignature]:
        """Stat signature of history.jsonl now, or None if it cannot be stat'ed.

        Lets a caller remember which version of the file an answer came
        from and skip its own scan while the file is unchanged.
        """
        return stat_signature(self._path)

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

        entries, by_sid, by_project = self._snapshot()
        positions: List[int] = []
        if owned_ids:
            # Precise: only count interactions from this session's own Claude sessions
            for sid in owned_ids:
                positions.extend(by_sid.get(sid, ()))
        else:
            # Fallback: directory matching for sessions without tracked IDs.
            # Each distinct raw project string is resolved once (memoised).
            session_dir = resolve_project_path(session.start_directory)
            for project, found in by_project.items():
                if resolve_project_path(project) == session_dir:
                    positions.extend(found)
        positions.sort()  # file order, as a scan would produce

        return [
            entries[i] for i in positions if entries[i].timestamp_ms >= session_start_ms
        ]

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

        session_dir = resolve_project_path(directory)
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
                    entry_dir = resolve_project_path(project)
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

    The answer is remembered per path: a transcript's first line never
    changes once written, so the cache is keyed on the file's inode (a
    replaced file is re-read) and filled only from a complete first line
    (one ending in a newline — a half-written one is re-read next time).
    Without it every stats sweep and every 1 Hz burn pass re-opened each
    compact/aside file of every live agent.
    """
    name = subagent_file.name
    if not (name.startswith("agent-acompact-") or name.startswith("agent-aside_question-")):
        return False
    try:
        ino = subagent_file.stat().st_ino
    except OSError:
        return False
    key = str(subagent_file)
    with _dir_cache_lock:
        hit = _duplicate_subagent_cache.get(key)
        if hit is not None and hit[0] == ino:
            return hit[1]
    # Read only the first line to check isMeta — fast even for huge files
    try:
        with open(subagent_file, 'r') as f:
            first_line = f.readline()
        stripped = first_line.strip()
        if not stripped:
            return False
        data = json.loads(stripped)
        result = isinstance(data, dict) and bool(data.get("isMeta"))
    except (IOError, json.JSONDecodeError, TypeError):
        return False
    if first_line.endswith("\n"):
        with _dir_cache_lock:
            if len(_duplicate_subagent_cache) >= _DIR_CACHE_MAX:
                _duplicate_subagent_cache.clear()
            _duplicate_subagent_cache[key] = (ino, result)
    return result


# Directory listings and first-line checks reused while nothing changed.
# A directory's mtime moves when an entry is created, deleted or renamed —
# the only ways its listing can change — so a glob is repeated only then.
# Bounded by a flush at _DIR_CACHE_MAX entries (each is a few hundred
# bytes; a flush costs one re-listing per directory).
_dir_cache_lock = threading.Lock()
_dir_listing_cache: Dict[Tuple[str, str], Tuple[FileSignature, List[Path]]] = {}
_duplicate_subagent_cache: Dict[str, Tuple[int, bool]] = {}
_DIR_CACHE_MAX = 8192


def _cached_glob(directory: Path, pattern: str) -> List[Path]:
    """``list(directory.glob(pattern))``, reused while the directory is unchanged.

    Returns ``[]`` for a missing directory (what ``exists()`` + glob gave).
    A listing is remembered only once the directory's mtime has settled
    (see ``stat_gate``), so an entry created in the same coarse mtime tick
    as the listing cannot be missed. Callers iterate the returned list;
    they must not mutate it.
    """
    try:
        sig = FileSignature.of(directory.stat())
    except OSError:
        return []
    key = (str(directory), pattern)
    with _dir_cache_lock:
        hit = _dir_listing_cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    listing = list(directory.glob(pattern))
    if is_settled(sig):
        with _dir_cache_lock:
            if len(_dir_listing_cache) >= _DIR_CACHE_MAX:
                _dir_listing_cache.clear()
            _dir_listing_cache[key] = (sig, listing)
    return listing


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
    resolved = resolve_project_path(path)
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

    Claude Code writes one line per content block of an assistant message,
    each repeating the whole message's usage: a message is counted once,
    by ``message.id``, at its last line's usage and its first line's time
    (``transcript_index``). A line without an id or a parseable timestamp
    counts on its own. ``cache_creation_1h_tokens`` is the 1-hour TTL part
    of the cache writes (``usage.cache_creation``); the ``long_*`` counts
    the part spent by long-context requests (``transcript_index``).

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
        "cache_creation_1h_tokens": 0,
        **dict.fromkeys(LONG_KEYS, 0),
        "current_context_tokens": 0,
        "model": None,
        "provider": None,
        "effort": None,
        "pr_number": None,
    }
    token_keys = ("input_tokens", "output_tokens", "cache_creation_tokens",
                  "cache_read_tokens", "cache_creation_1h_tokens")
    # message id -> (its first line's time, its last line's usage)
    messages: Dict[str, Tuple[datetime, Tuple[int, ...]]] = {}

    user_prompt_times: List[datetime] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            msg_type = data.get("type")

            if msg_type == "assistant":
                message = data.get("message", {})
                usage = message.get("usage", {})
                if not usage:
                    continue
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_creation = usage.get(
                    "cache_creation_input_tokens", 0
                )
                line_usage = (input_tokens, output_tokens, cache_creation, cache_read,
                              cache_write_1h(usage, cache_creation))
                msg_time = parse_message_time(data.get("timestamp"))
                msg_id = message.get("id")
                previous = (0, 0, 0, 0, 0)
                if msg_time is not None and isinstance(msg_id, str) and msg_id:
                    if msg_id in messages:
                        first_time, previous = messages[msg_id]
                    else:
                        first_time = msg_time
                    messages[msg_id] = (first_time, line_usage)
                    msg_time = first_time
                # Filter by the message's time, when it has one
                if since and msg_time is not None and msg_time < since:
                    continue

                for key, now, before in zip(token_keys, line_usage, previous):
                    totals[key] += now - before
                if is_long_request(message.get("model"), input_tokens, cache_creation,
                                   cache_read):
                    for key, now, before in zip(LONG_KEYS, line_usage, previous):
                        totals[key] += now - before
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
                    # Reasoning effort in force for this turn (#497) —
                    # a top-level field on the assistant record.
                    effort = data.get("effort")
                    if effort and isinstance(effort, str):
                        totals["effort"] = effort

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

            elif msg_type == "pr-link":
                # The PR Claude Code linked the session to — what its
                # status line shows as "PR #N" (#489)
                pr = data.get("prNumber")
                if isinstance(pr, int):
                    totals["pr_number"] = pr

        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Calculate durations between consecutive prompts
    work_times = []
    for i in range(1, len(user_prompt_times)):
        duration = (user_prompt_times[i] - user_prompt_times[i - 1]).total_seconds()
        if duration > 0:
            work_times.append(duration)

    return totals, work_times


# Every transcript-derived number is read through one incremental index per
# file (transcript_index.py, audit R1/R3): a query costs the bytes appended
# since the previous query of that file, never the file's size. This replaced
# the per-path (mtime, size, since) result cache, which re-parsed an active
# transcript whole on every change and held at most 512 entries.
_transcripts = TranscriptRegistry()


def clear_transcript_caches() -> None:
    """Drop every transcript index and path/directory/first-line cache (tests, benchmarks)."""
    _transcripts.clear()
    with _dir_cache_lock:
        _dir_listing_cache.clear()
        _duplicate_subagent_cache.clear()
    with _resolved_paths_lock:
        _resolved_paths.clear()


def read_session_file_stats(
    session_file: Path,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Read token usage and work times from a session file.

    Answers from the file's :class:`~overcode.transcript_index.TranscriptIndex`,
    which parses only the bytes appended since it was last asked, so a
    static file costs one ``stat`` per call and an active one costs its
    new lines. Output is identical to :func:`_read_session_file_stats_full`
    (the single-pass parse this wrapped before), which is kept as the
    reference and used directly for a timezone-aware ``since`` — the
    full parser's naive/aware comparison error is not worth reproducing.

    Args:
        session_file: Path to the session JSONL file
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    if since is not None and getattr(since, "tzinfo", None) is not None:
        return _read_session_file_stats_full(session_file, since)
    return _transcripts.stats(session_file, since)


def _read_session_file_stats_full(
    session_file: Path,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Full single-pass parse of a session file — the reference for the index."""
    try:
        session_file.stat()
    except OSError:
        # Missing/unreadable file — matches the old exists() short-circuit.
        return empty_stats()
    try:
        with open(session_file, 'r') as f:
            return _parse_session_lines(f, since=since)
    except IOError:
        return empty_stats()


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
        return empty_stats()

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


# The version of what this reader reports per conversation
# (``conversation_spend``): 2 counts each message once by ``message.id``
# across the conversation and all its subagents (workflow agents included)
# and splits 1-hour cache writes out; 3 also leaves out the history a
# conversation copied when it was forked from an earlier one (``/branch``,
# ``--fork-session``: the copy keeps the original message ids). An entry
# read by an older version is replaced by the next read of its transcript,
# not max'd with it.
CLAUDE_READER_VERSION = 3

# How deep under ``<session>/subagents/`` agent transcripts are looked for
# (workflow agents sit at ``subagents/workflows/<run id>/agent-*.jsonl``)
SUBAGENT_DEPTH = 3


def _cached_subdirs(directory: Path) -> List[Path]:
    """The subdirectories of ``directory``, reused while it is unchanged (see ``_cached_glob``)."""
    try:
        sig = FileSignature.of(directory.stat())
    except OSError:
        return []
    key = (str(directory), "/")
    with _dir_cache_lock:
        hit = _dir_listing_cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    try:
        with os.scandir(directory) as entries:
            listing = sorted(Path(e.path) for e in entries if e.is_dir(follow_symlinks=False))
    except OSError:
        return []
    if is_settled(sig):
        with _dir_cache_lock:
            if len(_dir_listing_cache) >= _DIR_CACHE_MAX:
                _dir_listing_cache.clear()
            _dir_listing_cache[key] = (sig, listing)
    return listing


def subagent_files(subagents_dir: Path, depth: int = SUBAGENT_DEPTH) -> List[Path]:
    """Every subagent transcript under ``subagents_dir``, recursively, sorted.

    ``agent-*.jsonl`` at the top (subagents, forks, compaction calls) and in
    subdirectories (``workflows/<run id>/``). The compaction and
    side-question copies of the parent's messages (``_is_duplicate_subagent``)
    are left out. Listings are cached per directory, so an unchanged tree
    costs one stat per directory.
    """
    found = [f for f in _cached_glob(subagents_dir, "agent-*.jsonl")
             if not _is_duplicate_subagent(f)]
    if depth > 0:
        for sub in _cached_subdirs(subagents_dir):
            found.extend(subagent_files(sub, depth - 1))
    return sorted(found)


def subagent_key(session_id: str, subagents_dir: Path, path: Path) -> str:
    """A subagent's conversation key: ``<session id>/agent-x`` (``.../workflows/<run>/agent-x``)."""
    try:
        rel = path.relative_to(subagents_dir).with_suffix("")
        return f"{session_id}/{rel.as_posix()}"
    except ValueError:
        return f"{session_id}/{path.stem}"


def _tree_copies(files: List[Path], transcripts: TranscriptRegistry, wanted=None,
                 refreshed: bool = False) -> Dict[int, List[Tuple[int, Tuple[int, ...]]]]:
    """The messages each file (by position) holds that another file of the tree also holds.

    ``files`` is a conversation (first) and its subagents. A file that
    copies messages copies them at its start (a fork opens with its
    parent's last message: same ``message.id``, timestamp and usage), so
    only each subagent's first messages (``TranscriptIndex.head``) are
    looked for, in the files whose time span covers them
    (``TranscriptIndex.holds``). A message is kept by the file it first
    appeared in: the conversation, else the file that does not hold it at
    its start, else the earlier file. ``wanted(key)`` limits which heads
    are looked for (the window skips messages before it); ``refreshed``
    says every file was just read. Returns ``{position: [(key, usage),
    ...]}`` to take off those files.
    """
    if len(files) < 2:
        return {}
    raw = [[]] + [transcripts.head(path, refresh=not refreshed) for path in files[1:]]
    pairs = sorted((key, h, i) for i, head in enumerate(raw) for h, key, _ in head
                   if wanted is None or wanted(key))
    if not pairs:
        return {}
    pair_keys = [key for key, _, _ in pairs]
    starts = [{h for h, _, _ in head} for head in raw]
    if not refreshed:
        transcripts.head(files[0])  # brings the conversation's index up to date
    holders: Dict[int, List[int]] = {}  # hash -> positions of the files holding it
    for j, path in enumerate(files):
        index = transcripts.get(path)
        span = index.span()
        if span is None:
            continue
        # A copy is dated at or after its original, which is in the file's span
        lo = bisect.bisect_left(pair_keys, span[0])
        hi = bisect.bisect_right(pair_keys, key_before(span[1], -COPY_WINDOW_SECONDS))
        asked = [(h, key) for key, h, i in pairs[lo:hi] if i != j]
        if not asked:
            continue
        for h in index.holds(asked, refresh=False):
            holders.setdefault(h, []).append(j)
    copies: Dict[int, List[Tuple[int, Tuple[int, ...]]]] = {}
    for i in range(1, len(files)):
        for h, key, usage in raw[i]:
            if wanted is not None and not wanted(key):
                continue
            for j in holders.get(h, ()):
                if j != i and (j == 0 or h not in starts[j] or j < i):
                    copies.setdefault(i, []).append((key, usage))
                    break
    return copies


_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens",
               "cache_read_tokens", "cache_creation_1h_tokens")


def _take_off(totals: dict, usage: Tuple[int, ...]) -> None:
    """Take a copied message's usage (``TranscriptIndex.head``) off a file's totals."""
    for name, value in zip(_USAGE_KEYS, usage):
        totals[name] = max(0, totals.get(name, 0) - value)
    if len(usage) > len(_USAGE_KEYS) and usage[len(_USAGE_KEYS)]:
        # A long-context request: off the breakdown too, where it is kept
        for name, value in zip(LONG_KEYS, usage):
            if name in totals:
                totals[name] = max(0, totals[name] - value)


def long_usage(read: dict) -> Dict[str, int]:
    """A read's long-context breakdown, by ``conversation_spend.USAGE_KEYS``."""
    return {key: read.get(name, 0) or 0
            for key, name in zip(("in", "out", "cw", "cr", "cw1h"), LONG_KEYS)}


def read_tree_stats(
    files: List[Path],
    since: Optional[datetime] = None,
    transcripts: Optional[TranscriptRegistry] = None,
    earlier: Sequence[Path] = (),
) -> List[Tuple[dict, List[float]]]:
    """``read_session_file_stats`` of each of a conversation's files, each message once.

    ``files`` is the conversation's transcript then its subagents'
    (``subagent_files``); a message one file copied from another counts
    in the file it first appeared in (``_tree_copies``). Within a file
    each message counts once already (``transcript_index``).

    ``earlier`` are the conversations this one may have been forked from
    (the same agent's earlier ones, its parent's; a background session's
    agent's): a fork's copy of their history is theirs, not its own, and
    is taken off its transcript's totals (``TranscriptRegistry.fork_prefix``).
    """
    registry = transcripts if transcripts is not None else _transcripts
    if since is not None and getattr(since, "tzinfo", None) is not None:
        return [_read_session_file_stats_full(path, since) for path in files]
    results = [registry.stats(path, since) for path in files]
    copies = _tree_copies(files, registry, refreshed=True)
    since_key = None if since is None else datetime_key(since)
    if copies:
        for i, messages in copies.items():
            totals = dict(results[i][0])
            for key, usage in messages:
                if since_key is None or key >= since_key:
                    _take_off(totals, usage)
            results[i] = (totals, results[i][1])
    if earlier and files:
        copied = registry.fork_prefix(files[0], earlier)
        if copied:
            totals = dict(results[0][0])
            _take_off_prefix(totals, registry.prefix_usage(files[0], copied, since_key))
            results[0] = (totals, results[0][1])
    return results


def _take_off_prefix(totals: dict, usage: Tuple[int, ...]) -> None:
    """Take a fork's copied prefix (``TranscriptIndex.prefix_usage``) off its totals."""
    for name, value in zip(_USAGE_KEYS + LONG_KEYS, usage):
        if name in totals:
            totals[name] = max(0, totals[name] - value)


def conversation_order(interactions: Sequence["HistoryEntry"]) -> List[str]:
    """An agent's conversation ids in the order they began (first history entry).

    A fork begins after the conversation it was forked from, so the
    original always comes first (``read_tree_stats``'s ``earlier``).
    """
    return list(dict.fromkeys(e.session_id for e in interactions if e.session_id))


def get_session_stats(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH,
    projects_path: Path = CLAUDE_PROJECTS_PATH,
    history_file: Optional["HistoryFile"] = None,
    transcripts: Optional[TranscriptRegistry] = None,
    fork_sources: Sequence[str] = (),
) -> Optional[AgentSessionStats]:
    """Get comprehensive stats for an overcode session.

    Combines interaction counting with token usage from session files.

    Session scoping: get_interactions_for_session() is the single source of
    truth for which Claude Code sessions belong to this overcode session.
    When agent_session_ids are tracked, it filters precisely by sessionId;
    otherwise falls back to directory+timestamp matching (#119, #264).

    Context window uses active_agent_session_id after /clear (#116),
    falling back to MAX across all matched sessions.

    Each conversation is read with all its subagents (``subagents/**``,
    workflow agents included), each message once (``read_tree_stats``),
    and reported per conversation at ``CLAUDE_READER_VERSION``. A
    conversation forked from one that began before it (the agent's own,
    in the order they began, ``conversation_order``) or from one of
    ``fork_sources`` (its parent's conversations: ``overcode fork``) does
    not count the history it copied (``read_tree_stats``'s ``earlier``).

    Args:
        session: The overcode Session
        history_path: Path to history.jsonl
        projects_path: Path to Claude projects directory
        history_file: Optional HistoryFile for cached access (avoids re-parsing)
        transcripts: the transcript indexes to read through (default: the
            module's; a one-off pass over old records passes its own)
        fork_sources: other agents' conversation ids this agent's may
            have been forked from (its parent's)

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
    session_ids = conversation_order(interactions)
    sid_to_project: Dict[str, str] = {}
    for e in interactions:
        if e.session_id and e.project:
            sid_to_project[e.session_id] = e.project
    # The transcripts read so far, in the order the conversations began:
    # the ones a later conversation may have been forked from
    began_before: List[Path] = []

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
    detected_effort: Optional[str] = None
    detected_pr: Optional[int] = None
    all_work_times: List[float] = []
    subagent_count = 0  # Count subagent files (#176)
    live_subagent_count = 0  # Subagents with recently-modified files (#256)
    background_task_count = 0  # Count background task files (#177)
    now = time.time()
    # Spend per conversation: only transcripts that are there (a pruned one
    # is not read as zero; its spend on record stays)
    per_conversation: Dict[str, Dict[str, Any]] = {}

    def conversation_usage(read: dict, model: Optional[str]) -> Dict[str, Any]:
        return conversation_spend.usage(
            read["input_tokens"], read["output_tokens"],
            read["cache_creation_tokens"], read["cache_read_tokens"],
            model=model, cache_creation_1h_tokens=read.get("cache_creation_1h_tokens", 0),
            rv=CLAUDE_READER_VERSION, long=long_usage(read))

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
        transcript_exists = session_file.exists()

        # Subagents in {sessionId}/subagents/ (recursively: workflow agents
        # sit in workflows/<run id>/), beside the actual project path where
        # the session file was found.
        actual_project = sid_to_project.get(sid, session.start_directory)
        encoded = encode_project_path(actual_project)
        subagents_dir = projects_path / encoded / sid / "subagents"
        subs = subagent_files(subagents_dir)
        earlier = [session_file.parent / f"{cid}.jsonl" for cid in fork_sources
                   if isinstance(cid, str) and cid and cid != sid] + began_before
        reads = read_tree_stats([session_file, *subs], since=session_start,
                                transcripts=transcripts, earlier=earlier)
        if transcript_exists:
            began_before.append(session_file)
        usage, work_times = reads[0]
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
                if usage.get("effort"):
                    detected_effort = usage.get("effort")
                if usage.get("pr_number"):
                    detected_pr = usage["pr_number"]
        else:
            if usage["current_context_tokens"] > current_context:
                current_context = usage["current_context_tokens"]
            if usage["model"]:
                detected_model = usage["model"]
            if usage["provider"]:
                detected_provider = usage["provider"]
            if usage.get("effort"):
                detected_effort = usage.get("effort")
            if usage.get("pr_number"):
                detected_pr = usage["pr_number"]

        # Collect work times from this session file
        all_work_times.extend(work_times)

        for subagent_file, (sub_usage, _) in zip(subs, reads[1:]):
            subagent_count += 1
            try:
                if now - subagent_file.stat().st_mtime < 30:
                    live_subagent_count += 1
            except OSError:
                pass
            total_input += sub_usage["input_tokens"]
            total_output += sub_usage["output_tokens"]
            total_cache_creation += sub_usage["cache_creation_tokens"]
            total_cache_read += sub_usage["cache_read_tokens"]
            # A subagent runs its own model (an Explore on haiku under an
            # opus parent): its own conversation, priced at that model
            if any(sub_usage[key] for key in _USAGE_KEYS[:4]):
                per_conversation[subagent_key(sid, subagents_dir, subagent_file)] = (
                    conversation_usage(sub_usage, sub_usage.get("model") or usage.get("model")))
        if transcript_exists:
            per_conversation[sid] = conversation_usage(usage, usage.get("model"))

        # Check for background tasks (run_in_background agents) (#177)
        # These are subagents that were started in background mode
        tasks_dir = projects_path / encoded / sid / "tasks"
        background_task_count += len(_cached_glob(tasks_dir, "task-*.jsonl"))

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
        effort=detected_effort,
        pr_number=detected_pr,
        last_command=last_command,
        per_conversation=per_conversation,
    )


def _version1_file_usage(path: Path, since: Optional[datetime]) -> Optional[dict]:
    """What reader version 1 read from one transcript: every assistant line added up.

    Version 1 added each line's usage, so a message written as several
    content blocks counted several times. Kept only to restate records
    from before per-conversation spend (``legacy_session_reads``). None
    when the file cannot be read.
    """
    totals = {"in": 0, "out": 0, "cw": 0, "cr": 0}
    model = None
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") != "assistant":
                        continue
                    if since is not None:
                        msg_time = parse_message_time(data.get("timestamp"))
                        if msg_time is not None and msg_time < since:
                            continue
                    message = data.get("message") or {}
                    usage = message.get("usage") or {}
                    counts = [usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                              usage.get("cache_creation_input_tokens", 0),
                              usage.get("cache_read_input_tokens", 0)]
                    if not all(isinstance(c, int) for c in counts):
                        continue
                    for key, value in zip(("in", "out", "cw", "cr"), counts):
                        totals[key] += value
                    if sum(counts) > 0 and message.get("model"):
                        model = message.get("model")
                except (ValueError, AttributeError, TypeError):
                    continue
    except OSError:
        return None
    return {**totals, "model": model}


def legacy_session_reads(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH,
    projects_path: Optional[Path] = None,
    history_file: Optional["HistoryFile"] = None,
) -> Dict[str, Dict[str, Any]]:
    """What reader version 1 read per conversation, for the transcripts still there.

    The conversation and its top-level ``subagents/agent-*.jsonl`` (the
    compaction copies left out; version 1 did not look deeper), every line
    added up. A record from before per-conversation spend holds those
    figures in its totals: folding them into entries first lets a newer
    read replace exactly them (``spend_recompute``).
    """
    if projects_path is None:
        projects_path = CLAUDE_PROJECTS_PATH
    if not session.start_directory:
        return {}
    try:
        start_local = datetime.fromisoformat(session.start_time)
        since = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return {}
    hf = history_file or (
        _default_history if history_path == CLAUDE_HISTORY_PATH else HistoryFile(history_path))
    interactions = hf.get_interactions_for_session(session)
    sid_to_project: Dict[str, str] = {}
    for e in interactions:
        if e.session_id and e.project:
            sid_to_project[e.session_id] = e.project
    reads: Dict[str, Dict[str, Any]] = {}
    for sid in {e.session_id for e in interactions if e.session_id}:
        session_file = get_session_file_path(session.start_directory, sid, projects_path)
        if not session_file.exists() and sid_to_project.get(sid):
            session_file = get_session_file_path(sid_to_project[sid], sid, projects_path)
        parent = _version1_file_usage(session_file, since) if session_file.exists() else None
        encoded = encode_project_path(sid_to_project.get(sid, session.start_directory))
        subagents_dir = projects_path / encoded / sid / "subagents"
        for sub_file in _cached_glob(subagents_dir, "agent-*.jsonl"):
            if _is_duplicate_subagent(sub_file):
                continue
            sub = _version1_file_usage(sub_file, since)
            if sub and any(sub[k] for k in ("in", "out", "cw", "cr")):
                reads[f"{sid}/{sub_file.stem}"] = conversation_spend.usage(
                    sub["in"], sub["out"], sub["cw"], sub["cr"],
                    model=sub["model"] or (parent or {}).get("model"))
        if parent is not None:
            reads[sid] = conversation_spend.usage(
                parent["in"], parent["out"], parent["cw"], parent["cr"], model=parent["model"])
    return reads


def read_window_token_usage(
    session_file: Path,
    since: datetime,
) -> dict:
    """Sum token usage for assistant messages timestamped at or after ``since``.

    Used by the burn-rate calculation (#174), once a second for every owned
    transcript of every awake agent. Answers from the file's
    :class:`~overcode.transcript_index.TranscriptIndex`: the per-message
    usage log is kept in timestamp order, so a call is one ``stat`` (plus
    the appended lines, if any) and a bisect — not a re-parse of the file.
    Output is identical to :func:`_read_window_token_usage_full`, the full
    pass this wrapped before, kept as the reference and used for a
    timezone-aware ``since``.

    ``since`` should be a LOCAL-naive datetime. Each message's UTC timestamp
    is converted to the local zone and stripped of tzinfo before comparison,
    matching the convention in _parse_session_lines.

    Returns dict with input_tokens, output_tokens, cache_creation_tokens,
    cache_read_tokens (all zero if the file is missing or unreadable).
    """
    if getattr(since, "tzinfo", None) is not None:
        return _read_window_token_usage_full(session_file, since)
    return _transcripts.window_usage(session_file, since)


def _read_window_token_usage_full(
    session_file: Path,
    since: datetime,
) -> dict:
    """Full single-pass window sum — the reference for the index.

    A message counts once (by ``message.id``: its last line's usage, its
    first line's time), as in ``_parse_session_lines``.
    """
    totals = empty_window()
    if not session_file.exists():
        return totals
    keys = tuple(totals)
    messages: Dict[str, Tuple[datetime, Tuple[int, ...]]] = {}

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
                    msg_time = parse_message_time(data.get("timestamp"))
                    if msg_time is None:
                        continue
                    message = data.get("message", {})
                    usage = message.get("usage") or {}
                    cache_creation = usage.get("cache_creation_input_tokens", 0)
                    line_usage = (usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                                  cache_creation, usage.get("cache_read_input_tokens", 0),
                                  cache_write_1h(usage, cache_creation))
                    previous = (0, 0, 0, 0, 0)
                    msg_id = message.get("id")
                    if isinstance(msg_id, str) and msg_id:
                        if msg_id in messages:
                            msg_time, previous = messages[msg_id]
                        messages[msg_id] = (msg_time, line_usage)
                    if msg_time < since:
                        continue
                    for key, now, before in zip(keys, line_usage, previous):
                        totals[key] += now - before
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

    A file whose mtime is more than an hour before ``since`` is not opened:
    every message in it was written before the window started (a message's
    timestamp is taken just before the line is appended, on the same
    clock), so it contributes nothing. The hour covers a DST fold in the
    local-naive ``since``, coarse mtime stamps, and small clock skew; a
    transcript copied from a host whose clock ran more than an hour slow
    relative to its mtimes could still be skipped wrongly. Every other
    file is answered by its incremental index (read_window_token_usage).
    Subagent listings and the duplicate-subagent check are cached, so an
    unchanged tree costs one stat per directory and file. Each message
    counts once, as in ``get_session_stats`` (``_tree_copies``).

    Returns dict with input_tokens, output_tokens, cache_creation_tokens,
    cache_read_tokens and cache_creation_1h_tokens (the 1-hour TTL part of
    the cache writes) — totals over messages timestamped at or after ``since``.
    """
    # Resolve the projects path at call time so monkeypatching
    # CLAUDE_PROJECTS_PATH (in tests) takes effect without each caller
    # having to thread it through.
    if projects_path is None:
        projects_path = CLAUDE_PROJECTS_PATH

    totals = empty_window()
    if not session.start_directory:
        return totals

    sids = list(getattr(session, 'agent_session_ids', None) or [])
    if not sids:
        return totals

    mtime_floor = _window_mtime_floor(since)
    aware = getattr(since, "tzinfo", None) is not None
    since_key = None if aware else datetime_key(since)
    for sid in sids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        try:
            st = session_file.stat()
        except OSError:
            continue
        reads: List[dict] = []
        if st.st_mtime >= mtime_floor:
            reads.append(read_window_token_usage(session_file, since))
        else:
            reads.append(empty_window())

        # Include subagent files (parallel workflows, workflow agents),
        # skipping duplicate compaction/side-question logs that copy
        # parent messages.
        encoded = encode_project_path(session.start_directory)
        subagents_dir = projects_path / encoded / sid / "subagents"
        files = [session_file]
        for sub_file in subagent_files(subagents_dir):
            try:
                st = sub_file.stat()
            except OSError:
                continue  # vanished since the listing: reads as zeros anyway
            if st.st_mtime < mtime_floor:
                continue
            files.append(sub_file)
            reads.append(read_window_token_usage(sub_file, since))

        # A message a subagent copied (a fork's first) counts once
        if since_key is not None and len(files) > 1:
            copies = _tree_copies(files, _transcripts, wanted=lambda key: key >= since_key)
            for i, messages in copies.items():
                for _key, usage in messages:
                    _take_off(reads[i], usage)
        for u in reads:
            for k in totals:
                totals[k] += u.get(k, 0)

    return totals


# Margin under the window start below which a file's mtime proves it holds
# nothing in the window. One hour covers a DST fold of the local-naive
# ``since``, whole-second mtime stamps, and small clock skew.
WINDOW_MTIME_MARGIN_SECONDS = 3600.0


def _window_mtime_floor(since: datetime) -> float:
    """Epoch seconds: files last modified before this cannot hold window messages."""
    try:
        return since.timestamp() - WINDOW_MTIME_MARGIN_SECONDS
    except (OverflowError, OSError, ValueError):
        return float("-inf")
