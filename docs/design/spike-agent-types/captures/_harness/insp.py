"""inspect a spike run: python3 insp.py <run> [hooks|files|tx <path>|stream]"""
import glob
import json
import os
import sys

S = os.path.dirname(os.path.abspath(__file__))
run = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "hooks"
d = os.path.join(S, "runs", run)
HOME = os.path.expanduser("~")


def short(v, n=160):
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= n else s[:n] + "..."


def proj_dirs():
    sids = set()
    for l in open(os.path.join(d, "hooks.jsonl")):
        r = json.loads(l)
        tp = r.get("transcript_path")
        if tp:
            sids.add(os.path.dirname(tp))
    return sorted(sids)


if mode == "hooks":
    t0 = None
    for l in open(os.path.join(d, "hooks.jsonl")):
        r = json.loads(l)
        ev = r.pop("_event"); t = r.pop("_t")
        t0 = t0 or t
        for k in ("session_id", "transcript_path", "cwd", "hook_event_name", "prompt_id"):
            r.pop(k, None)
        out = {k: short(v) for k, v in r.items()}
        sid = json.loads(l).get("session_id", "")[:8]
        print(f"+{t - t0:6.1f} {sid} {ev:16} {json.dumps(out)}")
elif mode == "files":
    for p in proj_dirs():
        print("PROJECT", p)
        for f in sorted(glob.glob(p + "/**", recursive=True)):
            if os.path.isfile(f):
                print("  ", f[len(p):], os.path.getsize(f))
        tmp = p.replace(HOME + "/.claude/projects/", "$TMPDIR_CLAUDE/")
        for f in sorted(glob.glob(tmp + "/**", recursive=True)):
            if os.path.isfile(f):
                print("  TMP", f[len(tmp):], os.path.getsize(f))
elif mode == "tx":
    path = sys.argv[3]
    for l in open(path):
        r = json.loads(l)
        keys = sorted(r.keys())
        msg = r.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        kinds = []
        if isinstance(content, list):
            for c in content:
                kinds.append(c.get("type") + (":" + c.get("name", "") if c.get("type") == "tool_use" else ""))
        elif isinstance(content, str):
            kinds.append("str:" + content[:60])
        usage = msg.get("usage") if isinstance(msg, dict) else None
        extra = {k: short(r[k], 80) for k in ("isSidechain", "agentId", "parentUuid", "sessionId", "subtype", "toolUseResult", "attachment", "teamName", "agentName") if k in r}
        print(r.get("type"), kinds, "usage=" + short(usage, 120) if usage else "", extra)
        print("     keys:", keys)
elif mode == "stream":
    for l in open(os.path.join(d, "stream.jsonl")):
        r = json.loads(l)
        if r.get("type") == "system" and r.get("subtype") in ("init", "hook_started", "hook_response"):
            continue
        print(r.get("type"), r.get("subtype"), short(r, 400))
