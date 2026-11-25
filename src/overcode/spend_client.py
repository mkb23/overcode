"""Record an eval's LLM spend to overcode: a stdlib-only helper.

Standalone on purpose: it imports nothing from overcode, so it can be
copied into an eval's own tree (``overcode spend helper-path`` prints this
file's path) and run in any virtualenv. It records through the ``overcode``
CLI (``overcode spend record --stdin``), which bills the agent this process
runs under (``docs/design/experiment-billing.md``)::

    from spend_client import SpendMeter      # or: from overcode.spend_client import ...

    meter = SpendMeter(label="sweep-a")       # one run; flushed every 30 s and at exit
    resp = client.messages.create(model=MODEL, ...)
    meter.add_usage(resp.usage, model=MODEL)  # Anthropic- or OpenAI-shaped usage
    meter.add(usd=0.42)                       # or a cost you know
    meter.close()

Do not meter spend overcode already reads: an overcode child agent, or
``claude -p`` run as one. Counted twice otherwise.

Usage is summed in memory and sent per model, so a flush costs one process
however many calls it covers. A flush that fails (no ``overcode`` on PATH,
nothing to bill) keeps the usage for the next one and warns once.
``stop_on_budget=True`` raises :class:`OverBudget` from ``add``/``flush``
once the agent's budget (evals included) is spent.
"""

from __future__ import annotations

import atexit
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

COMMAND_VAR = "OVERCODE_SPEND_CMD"  # the recorder, if not `overcode` on PATH


class OverBudget(RuntimeError):
    """The agent this eval bills to has spent its budget (evals included)."""


def _get(obj: Any, *names: str) -> int:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def usage_tokens(usage: Any) -> Dict[str, int]:
    """``{in, out, cache_write, cache_read}`` of an API response's ``usage``.

    Anthropic (``input_tokens``, ``output_tokens``,
    ``cache_creation_input_tokens``, ``cache_read_input_tokens``: input
    excludes the cached part) or OpenAI-style (``prompt_tokens``,
    ``completion_tokens``, ``prompt_tokens_details.cached_tokens``: prompt
    includes it). A dict or an object.
    """
    if _get(usage, "input_tokens", "output_tokens") or not _get(usage, "prompt_tokens",
                                                               "completion_tokens"):
        return {"in": _get(usage, "input_tokens"), "out": _get(usage, "output_tokens"),
                "cache_write": _get(usage, "cache_creation_input_tokens"),
                "cache_read": _get(usage, "cache_read_input_tokens")}
    details = (usage.get("prompt_tokens_details") if isinstance(usage, dict)
               else getattr(usage, "prompt_tokens_details", None)) or {}
    cached = _get(details, "cached_tokens")
    return {"in": max(0, _get(usage, "prompt_tokens") - cached),
            "out": _get(usage, "completion_tokens"), "cache_write": 0, "cache_read": cached}


class SpendMeter:
    """One eval run's spend, batched and recorded to overcode (module docstring)."""

    def __init__(self, label: Optional[str] = None, *, run: Optional[str] = None,
                 model: Optional[str] = None, provider: Optional[str] = None,
                 agent: Optional[str] = None, code: Optional[str] = None,
                 flush_seconds: float = 30.0, stop_on_budget: bool = False,
                 command: Optional[List[str]] = None):
        self.run = run or uuid.uuid4().hex[:12]
        self.label, self.model, self.provider = label, model, provider
        self.agent, self.code = agent, code
        self.flush_seconds = flush_seconds
        self.stop_on_budget = stop_on_budget
        env_cmd = os.environ.get(COMMAND_VAR)
        self.command = command or (shlex.split(env_cmd) if env_cmd else ["overcode"])
        self.last: Optional[dict] = None  # the recorder's last answer
        self.over_budget = False
        self._pending: Dict[Tuple[Optional[str], Optional[str]], Dict[str, float]] = {}
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._warned = False
        self._closed = False
        atexit.register(self._at_exit)

    # ── adding ──────────────────────────────────────────────────────────

    def add(self, model: Optional[str] = None, *, input_tokens: int = 0, output_tokens: int = 0,
            cache_write_tokens: int = 0, cache_read_tokens: int = 0,
            usd: Optional[float] = None, provider: Optional[str] = None) -> None:
        """Add one call's spend: tokens at ``model`` (default the meter's), or ``usd``."""
        model = model or self.model
        key = (None if usd is not None else model, provider or self.provider)
        with self._lock:
            entry = self._pending.setdefault(key, {})
            if usd is not None:
                entry["usd"] = entry.get("usd", 0.0) + float(usd)
            for name, value in (("in", input_tokens), ("out", output_tokens),
                                ("cache_write", cache_write_tokens),
                                ("cache_read", cache_read_tokens)):
                if value:
                    entry[name] = entry.get(name, 0) + int(value)
        self._maybe_flush()

    def add_usage(self, usage: Any, model: Optional[str] = None) -> None:
        """Add an API response's ``usage`` (Anthropic or OpenAI shape)."""
        t = usage_tokens(usage)
        self.add(model, input_tokens=t["in"], output_tokens=t["out"],
                 cache_write_tokens=t["cache_write"], cache_read_tokens=t["cache_read"])

    # ── flushing ────────────────────────────────────────────────────────

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush >= self.flush_seconds:
            self.flush()
        elif self.stop_on_budget and self.over_budget:
            raise OverBudget(self._budget_message())

    def _lines(self, pending) -> List[str]:
        lines = []
        for (model, provider), entry in pending.items():
            fields: Dict[str, Any] = {"run": self.run, **entry}
            if "usd" in entry and model is None:
                # A known cost: tokens (if any) are recorded, not priced
                fields["model"] = self.model
            elif model:
                fields["model"] = model
            for key, value in (("label", self.label), ("provider", provider),
                               ("agent", self.agent), ("code", self.code)):
                if value:
                    fields[key] = value
            lines.append(json.dumps(fields))
        return lines

    def flush(self) -> Optional[dict]:
        """Send what was added since the last flush; returns the recorder's answer."""
        with self._lock:
            pending, self._pending = self._pending, {}
            self._last_flush = time.monotonic()
        if not pending:
            return self.last
        lines = self._lines(pending)
        try:
            done = subprocess.run([*self.command, "spend", "record", "--stdin", "--json"],
                                  input="\n".join(lines) + "\n", capture_output=True,
                                  text=True, timeout=60)
            answer = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
            if done.returncode != 0:
                error = answer.get("error") if isinstance(answer, dict) else None
                raise RuntimeError(error or done.stderr.strip() or f"exit {done.returncode}")
        except Exception as e:  # keep the usage for the next flush
            with self._lock:
                for key, entry in pending.items():
                    mine = self._pending.setdefault(key, {})
                    for name, value in entry.items():
                        mine[name] = mine.get(name, 0) + value
            if not self._warned:
                self._warned = True
                print(f"spend_client: could not record spend to overcode ({e}); "
                      "kept for the next flush", file=sys.stderr)
            return self.last
        self.last = answer[-1] if isinstance(answer, list) and answer else answer
        if isinstance(self.last, dict):
            self.over_budget = bool(self.last.get("over_budget"))
        if self.stop_on_budget and self.over_budget:
            raise OverBudget(self._budget_message())
        return self.last

    def _budget_message(self) -> str:
        last = self.last or {}
        return (f"overcode agent '{last.get('agent_name')}' is over its budget "
                f"(${last.get('charged') or 0:.2f} of ${last.get('budget') or 0:.2f}, evals included)")

    def close(self) -> Optional[dict]:
        """Flush what is left; the meter takes no more."""
        self._closed = True
        try:
            return self.flush()
        finally:
            atexit.unregister(self._at_exit)

    def _at_exit(self) -> None:
        if not self._closed:
            try:
                self.stop_on_budget = False
                self.flush()
            except Exception:
                pass

    def __enter__(self) -> "SpendMeter":
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is not None:
            self.stop_on_budget = False  # don't mask the error with OverBudget
        self.close()
