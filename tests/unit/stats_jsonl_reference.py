"""The pre-#524/#525 full-scan parsers, kept verbatim as a test oracle.

``codex_stats._scan_rollout``, ``grok_stats._scan_updates`` and
``grok_stats._count_prompts`` used to re-read their log from byte 0 on every
call. They now fold incrementally (``backends/jsonl_tail.py``). These copies
of the old bodies are what the incremental readers must agree with exactly,
after any sequence of appends, truncations and replacements.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

_COST_TICKS_PER_USD = 1_000_000_000


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


def scan_rollout(path: Path) -> Dict[str, Any]:
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
        "effort": None,
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
            # Reasoning effort (#497): `effort` on the turn context, with the
            # collaboration-mode settings' `reasoning_effort` as fallback —
            # the same two places the model lives.
            effort = payload.get("effort")
            if not effort:
                collab = payload.get("collaboration_mode")
                settings = collab.get("settings") if isinstance(collab, dict) else None
                if isinstance(settings, dict):
                    effort = settings.get("reasoning_effort")
            if effort and isinstance(effort, str):
                out["effort"] = effort
            continue

        if etype == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            meta = payload.get("internal_chat_message_metadata_passthrough")
            kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
            if isinstance(kinds, list) and "user.text" in kinds:
                out["interaction_count"] += 1
            continue

    return out


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


def scan_updates(path: Path, *, since_ts: Optional[float] = None) -> Dict[str, Any]:
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


def count_prompts(project_dir_path: Path, session_id: str) -> int:
    path = project_dir_path / "prompt_history.jsonl"
    count = 0
    for entry in _iter_jsonl(path):
        if isinstance(entry, dict) and entry.get("session_id") == session_id:
            count += 1
    return count
