"""Poll a run's project dir once a second and log file sizes/mtimes + workflow json state."""
import glob
import json
import os
import sys
import time

run, secs = sys.argv[1], int(sys.argv[2])
out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", run + "-watch.jsonl"), "w")
pat = os.path.expanduser(f"~/.claude/projects/*spike-runs-{run}-work")
t0 = time.time()
prev = None
while time.time() - t0 < secs:
    snap = {}
    for p in glob.glob(pat):
        for f in glob.glob(p + "/**", recursive=True):
            if os.path.isfile(f) and not f.endswith(".js"):
                snap[f[len(p):]] = os.path.getsize(f)
        for wf in glob.glob(p + "/*/workflows/wf_*.json"):
            try:
                d = json.load(open(wf))
                snap["WF_STATE"] = [d.get("status"), [(a.get("label"), a.get("state")) for a in d.get("workflowProgress", []) if a.get("type") == "workflow_agent"]]
            except Exception as e:
                snap["WF_STATE"] = str(e)
    if snap != prev:
        out.write(json.dumps({"t": round(time.time() - t0, 1), "snap": snap}) + "\n")
        out.flush()
        prev = snap
    time.sleep(0.5)
