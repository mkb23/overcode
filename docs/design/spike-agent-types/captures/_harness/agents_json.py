"""Run `claude agents --json [--all]`, keep only the spike entry verbatim; summarise others by keys/kind only."""
import collections
import json
import os
import subprocess
import sys
import time

S = os.path.dirname(os.path.abspath(__file__))
tag = sys.argv[1]
extra = sys.argv[2:]
env = {k: os.environ[k] for k in ("HOME", "PATH", "USER") if k in os.environ}
t = time.time()
p = subprocess.run(["claude", "agents", "--json", *extra], capture_output=True, text=True, env=env, timeout=60)
dt = time.time() - t
try:
    data = json.loads(p.stdout)
except Exception:
    print("RAW", p.stdout[:2000], p.stderr[:2000]); sys.exit(1)
mine = [e for e in data if "spike" in json.dumps(e)]
others = [e for e in data if e not in mine]
summary = {
    "elapsed_s": round(dt, 2),
    "total_entries": len(data),
    "other_entries_key_sets": collections.Counter(json.dumps(sorted(e.keys())) for e in others),
    "other_entries_kind_status": collections.Counter(f"{e.get('kind')}/{e.get('status')}/{e.get('state')}" for e in others),
    "spike_entries": mine,
}
out = os.path.join(S, "runs", "bgsess", f"agents-{tag}.json")
json.dump(summary, open(out, "w"), indent=1, default=str)
print(json.dumps(summary, indent=1, default=str)[:4000])
