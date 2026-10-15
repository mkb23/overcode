"""Build the spend fixtures from the spike's transcript skeletons.

The spike (``docs/design/spike-agent-types/captures``, Claude Code 2.1.300)
ran each kind of delegated work once and kept a scrubbed skeleton of every
transcript: no message content, only the record shapes, ids, timestamps
and usage. This keeps what the spend readers look at and drops the rest:

- assistant lines: ``type``, ``timestamp``, ``isSidechain``, ``isMeta``,
  ``agentId``, ``sessionId`` and ``message`` (``id``, ``model``, ``usage``
  and the content block types);
- user lines: ``type``, ``timestamp``, ``isMeta`` and the content's shape
  (a prompt becomes ``"<prompt>"``, tool results keep their type);
- the ``fork-context-ref`` line a fork transcript opens with;
- the ``cost-state`` record Claude Code writes when the session ends: what
  Claude itself counted, and the expected totals of the tests.

Each capture becomes ``<capture>/<session id>.jsonl`` plus its subagents
under ``<session id>/subagents/`` (workflow agents under
``subagents/workflows/<run id>/``), the layout Claude Code writes, and
``expected.json`` maps each session id to its cost-state. Run from the
repo root: ``python tests/fixtures/spike_spend/generate.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAPTURES = ROOT / "docs" / "design" / "spike-agent-types" / "captures"
OUT = Path(__file__).resolve().parent

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens", "cache_creation")


def _assistant(record: dict) -> dict:
    message = record.get("message") or {}
    usage = message.get("usage") or {}
    kept = {k: record[k] for k in ("type", "timestamp", "isSidechain", "isMeta",
                                   "agentId", "sessionId") if k in record}
    kept["message"] = {
        "id": message.get("id"),
        "model": message.get("model"),
        "content": [{"type": block.get("type")} for block in message.get("content") or []
                    if isinstance(block, dict)],
        "usage": {k: usage[k] for k in USAGE_KEYS if k in usage},
    }
    return kept


def _user(record: dict) -> dict:
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        shape = [{"type": block.get("type")} for block in content if isinstance(block, dict)]
    else:
        shape = "<prompt>"
    kept = {k: record[k] for k in ("type", "timestamp", "isSidechain", "isMeta",
                                   "agentId", "sessionId") if k in record}
    kept["message"] = {"role": "user", "content": shape}
    return kept


def _cost_state(record: dict) -> dict:
    return {
        "type": "cost-state",
        "sessionId": record.get("sessionId"),
        "totalCostUSD": record.get("totalCostUSD"),
        "modelUsage": {
            model: {k: usage.get(k) for k in ("inputTokens", "outputTokens",
                                              "cacheReadInputTokens",
                                              "cacheCreationInputTokens", "costUSD")}
            for model, usage in (record.get("modelUsage") or {}).items()
        },
    }


def reduce(path: Path) -> list:
    lines = []
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        kind = record.get("type")
        if kind == "assistant":
            lines.append(_assistant(record))
        elif kind == "user":
            lines.append(_user(record))
        elif kind == "fork-context-ref":
            lines.append({k: record.get(k) for k in ("type", "agentId", "parentSessionId",
                                                     "parentLastUuid", "contextLength")})
        elif kind == "cost-state":
            lines.append(_cost_state(record))
    return lines


def main() -> None:
    expected: dict = {}
    for capture in sorted(p for p in CAPTURES.iterdir() if p.is_dir()):
        skeletons = capture / "transcript-skeletons"
        if not skeletons.is_dir():
            continue
        dest = OUT / capture.name
        if dest.exists():
            shutil.rmtree(dest)
        for skeleton in sorted(skeletons.glob("*.jsonl")):
            parts = skeleton.name[: -len(".jsonl")].split("__")
            target = dest.joinpath(*parts).with_suffix(".jsonl")
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = reduce(skeleton)
            target.write_text("".join(json.dumps(line) + "\n" for line in lines))
            if len(parts) == 1:
                costs = [line for line in lines if line.get("type") == "cost-state"]
                if costs:
                    expected.setdefault(capture.name, {})[parts[0]] = costs[-1]
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
