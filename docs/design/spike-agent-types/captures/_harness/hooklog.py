#!/usr/bin/env python3
"""Spike hook logger: append event name + stdin JSON to $SPIKE_HOOKLOG."""
import json
import os
import sys
import time

ev = sys.argv[1] if len(sys.argv) > 1 else "?"
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    data = {"_raw": raw}
out = os.environ.get("SPIKE_HOOKLOG") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hooks.jsonl")
rec = {"_event": ev, "_t": round(time.time(), 3)}
if isinstance(data, dict):
    rec.update(data)
else:
    rec["_data"] = data
with open(out, "a") as f:
    f.write(json.dumps(rec) + "\n")
