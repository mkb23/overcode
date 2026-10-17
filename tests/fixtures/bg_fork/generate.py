"""Build the synthetic fork transcripts in this directory (run once; output committed).

The record shapes of Claude Code 2.1.302, scrubbed: no content, synthetic
ids and usage. ``claude -p "ALPHA"`` wrote conversation ORIGINAL with one
assistant message; ``claude --bg --resume ORIGINAL "BRAVO"`` appended a
second to the same transcript (same session id, no copy);
``claude --bg --resume ORIGINAL --fork-session "CHARLIE"`` wrote FORK: a
copy of ORIGINAL's whole history (every line, the same ``uuid``s,
``message.id``s, timestamps and usage, ``sessionId`` rewritten, a
``sessionKind`` added) and then its own message. ``/bg`` from an
interactive session forks the same way.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINAL = "0f0f0f0f-0000-4000-8000-00000000a1fa"
FORK = "0f0f0f0f-0000-4000-8000-0000000c4a71"
MODEL = "claude-haiku-5-5"


def _usage(inp, cw, cr, out):
    return {"input_tokens": inp, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": out,
            "cache_creation": {"ephemeral_1h_input_tokens": cw, "ephemeral_5m_input_tokens": 0}}


def _turn(sid, n, ts_user, ts_reply, usage, parent, kind=None):
    """One prompt and its reply: user, attachment, assistant, turn_duration."""
    extra = {"sessionKind": kind} if kind else {}
    base = {"sessionId": sid, "cwd": "/work/repo", "version": "2.1.302",
            "gitBranch": "HEAD", "userType": "external", "entrypoint": "cli",
            "isSidechain": False, **extra}
    user = f"u{n}-0000-4000-8000-000000000000"
    att = f"t{n}-0000-4000-8000-000000000000"
    reply = f"r{n}-0000-4000-8000-000000000000"
    return [
        {**base, "type": "user", "uuid": user, "parentUuid": parent, "timestamp": ts_user,
         "message": {"role": "user", "content": "<prompt>"}},
        {**base, "type": "attachment", "uuid": att, "parentUuid": user, "timestamp": ts_user,
         "attachment": {"type": "<attachment>"}},
        {**base, "type": "assistant", "uuid": reply, "parentUuid": att, "timestamp": ts_reply,
         "requestId": f"req_{n}", "message": {
             "id": f"msg_synthetic_{n}", "type": "message", "role": "assistant", "model": MODEL,
             "content": [{"type": "text", "text": "<reply>"}], "usage": usage}},
        {**base, "type": "system", "subtype": "turn_duration", "uuid": f"s{n}", "parentUuid": reply,
         "timestamp": ts_reply, "durationMs": 5000, "isMeta": False},
    ], reply


def _meta(sid, kind):
    return [{"type": kind, "sessionId": sid}]


def build():
    alpha, a_leaf = _turn(ORIGINAL, 1, "2026-10-16T23:56:05.000Z", "2026-10-16T23:56:10.935Z",
                          _usage(2, 10288, 11228, 7), None)
    bravo, b_leaf = _turn(ORIGINAL, 2, "2026-10-16T23:56:18.000Z", "2026-10-16T23:56:23.293Z",
                          _usage(2, 24157, 14314, 7), a_leaf)
    original = (_meta(ORIGINAL, "queue-operation") + alpha + _meta(ORIGINAL, "cost-state")
                + bravo + _meta(ORIGINAL, "cost-state"))

    def copied(lines):
        return [dict(line, sessionId=FORK, sessionKind="bg") if "uuid" in line else None
                for line in lines]

    charlie, _ = _turn(FORK, 3, "2026-10-16T23:56:28.000Z", "2026-10-16T23:56:33.831Z",
                       _usage(2, 16981, 21537, 8), b_leaf, kind="bg")
    fork = (_meta(FORK, "ai-title") + _meta(FORK, "mode")
            + [line for line in copied(alpha + bravo) if line is not None]
            + charlie + _meta(FORK, "cost-state"))
    for name, lines in (("original", original), ("fork", fork)):
        (HERE / f"{name}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))


if __name__ == "__main__":
    build()
