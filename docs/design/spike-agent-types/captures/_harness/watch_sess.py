"""Poll ~/.claude/sessions/*.json for entries whose cwd contains 'spike-runs' or '/spike/runs/'; log changes."""
import glob
import json
import os
import sys
import time

secs = int(sys.argv[1])
out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", "sessreg-watch.jsonl"), "w")
t0 = time.time()
prev = None
while time.time() - t0 < secs:
    snap = []
    for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if "/spike/runs/" in d.get("cwd", ""):
            snap.append(d)
    if snap != prev:
        out.write(json.dumps({"t": round(time.time() - t0, 1), "entries": snap}) + "\n")
        out.flush()
        prev = snap
    time.sleep(0.25)
