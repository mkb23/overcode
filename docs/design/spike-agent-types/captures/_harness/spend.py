"""Compare token sums: naive per-line vs dedup by message.id vs cost-state, for a run."""
import glob
import json
import os
import sys

run = sys.argv[1]
proj = glob.glob(os.path.expanduser(f"~/.claude/projects/*spike-runs-{run}-work"))[0]
K = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def scan(path):
    naive = dict.fromkeys(K, 0)
    last = {}
    cost = None
    for l in open(path):
        r = json.loads(l)
        if r.get("type") == "assistant":
            u = r["message"].get("usage") or {}
            for k in K:
                naive[k] += u.get(k, 0) or 0
            last[r["message"]["id"]] = u
        if r.get("type") == "cost-state":
            cost = r
    return naive, last, cost


for main in sorted(glob.glob(proj + "/*.jsonl")):
    sid = os.path.basename(main)[:-6]
    files = [main] + sorted(glob.glob(f"{proj}/{sid}/subagents/**/agent-*.jsonl", recursive=True))
    tot_naive = dict.fromkeys(K, 0)
    dedup = {}
    cost = None
    for f in files:
        n, last, c = scan(f)
        cost = cost or c
        for k in K:
            tot_naive[k] += n[k]
        for mid, u in last.items():
            dedup[mid] = u  # cross-file dedup by message id
    tot_dedup = {k: sum((u.get(k, 0) or 0) for u in dedup.values()) for k in K}
    print(sid[:8], "files", len(files))
    print("  naive per-line sum  ", tot_naive)
    print("  dedup by message.id ", tot_dedup)
    if cost:
        mu = cost["modelUsage"]
        print("  cost-state          ", {m: {k: v for k, v in d.items() if k in ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens', 'costUSD')} for m, d in mu.items()}, "total", cost["totalCostUSD"])
