"""Model-based test of the opencode telemetry plugin (#474).

The live corpus in ``tests/fixtures_opencode_events/`` pins eleven
scenarios. This test generates hundreds more from a grammar of the *same*
event vocabulary — turns with tool calls, permission dialogs (allow once /
always / reject), ``task`` sub-agents with their own turns and permission
asks, double-Escape interrupts, provider errors, retry backoff, queued
prompts, ``/new``, resumed conversations, re-fired user messages, and the
deprecated ``session.idle`` present or absent — and replays every stream
through the REAL plugin, checking each publish against
``tests.opencode_oracle``.

Failures print the seed and the offending record, so a reproduction is one
``StreamGen(random.Random(seed)).stream()`` away.
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from overcode.backends.opencode import bundled_plugin_path
from tests.opencode_oracle import diff_publishes, describe

REPLAY = Path(__file__).parent.parent / "js" / "opencode_plugin_replay.mjs"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run the plugin")

TOOLS = ["read", "glob", "grep", "bash", "edit", "write", "webfetch", "list"]
PERMISSION_TOOLS = ["bash", "edit", "webfetch"]


class StreamGen:
    """Random but well-formed opencode hook traffic, in spy-record shape."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.n = 0

    # ── ids ──────────────────────────────────────────────────────────────
    def _id(self, prefix):
        self.n += 1
        return f"{prefix}_{self.n:06d}"

    # ── primitives (shapes copied from the v1.18.29 corpus) ──────────────
    @staticmethod
    def bus(type_, **props):
        return {"hook": "event", "event": {"type": type_, "properties": props}}

    def created(self, sid, parent=None):
        info = {"id": sid, "slug": "gen", "directory": "/proj", "version": "1.18.29"}
        if parent:
            info["parentID"] = parent
        return self.bus("session.created", sessionID=sid, info=info)

    def chat_message(self, sid, msg):
        return {
            "hook": "chat.message",
            "input": {"sessionID": sid, "agent": "build", "model": {"providerID": "openai", "modelID": "gpt-4o-mini"}},
            "output": {"message": {"id": msg, "role": "user", "sessionID": sid}, "parts": []},
        }

    def message_updated(self, sid, msg, role):
        return self.bus("message.updated", sessionID=sid, info={"id": msg, "role": role, "sessionID": sid})

    def status(self, sid, kind):
        st = {"type": kind}
        if kind == "retry":
            st.update({"attempt": 1, "message": "rate limited", "next": 1000})
        return self.bus("session.status", sessionID=sid, status=st)

    def idle(self, sid):
        return self.bus("session.idle", sessionID=sid)

    def tool_before(self, sid, tool, call):
        return {
            "hook": "tool.execute.before",
            "input": {"tool": tool, "sessionID": sid, "callID": call},
            "output": {"args": {"command": "echo hi"} if tool == "bash" else {"filePath": "/proj/x"}},
        }

    def tool_after(self, sid, tool, call):
        return {
            "hook": "tool.execute.after",
            "input": {"tool": tool, "sessionID": sid, "callID": call, "args": {}},
            "output": {"title": tool, "output": "ok", "metadata": {}},
        }

    def perm_asked(self, sid, tool, call, pid):
        return self.bus(
            "permission.asked", id=pid, sessionID=sid, permission=tool,
            patterns=["echo hi"], metadata={"command": "echo hi"},
            always=["*"], tool={"messageID": self._id("msg"), "callID": call},
        )

    def perm_replied(self, sid, pid, reply):
        return self.bus("permission.replied", sessionID=sid, requestID=pid, reply=reply)

    def error(self, sid, name):
        if name == "MessageAbortedError":
            err = {"name": name, "data": {}}
        else:
            err = {"name": name, "data": {"message": "Incorrect API key provided", "statusCode": 401}}
        return self.bus("session.error", sessionID=sid, error=err)

    def noise(self, sid):
        r = self.rng.random()
        if r < 0.3:
            return self.bus("session.updated", sessionID=sid, info={"id": sid, "title": "t"})
        if r < 0.6:
            return self.bus("message.part.updated", sessionID=sid, messageID=self._id("msg"), partType="text")
        if r < 0.8:
            return self.bus("session.diff", sessionID=sid, diff=[])
        if r < 0.9:
            return {"hook": "chat.params", "input": {"sessionID": sid, "agent": "build"}}
        return self.bus("plugin.added", id="core/x")

    # ── settle: the idle signals, in the observed order, one of them
    #    sometimes missing (deprecated session.idle gone; or an old build
    #    without session.status) — never both. ─────────────────────────────
    def settle(self, sid):
        r = self.rng.random()
        if r < 0.25:
            return [self.status(sid, "busy"), self.status(sid, "idle")]
        if r < 0.4:
            return [self.status(sid, "busy"), self.idle(sid)]
        return [self.status(sid, "busy"), self.status(sid, "idle"), self.idle(sid)]

    # ── grammar ──────────────────────────────────────────────────────────
    def turn(self, sid, depth=0, queued_ok=True):
        rng = self.rng
        msg = self._id("msg")
        out = [self.chat_message(sid, msg), self.message_updated(sid, msg, "user"), self.status(sid, "busy")]
        assistant = self._id("msg")
        out.append(self.message_updated(sid, assistant, "assistant"))
        if rng.random() < 0.7:
            out.append(self.message_updated(sid, msg, "user"))  # the observed early re-fire
        ended = False
        for _ in range(rng.randint(0, 4)):
            if rng.random() < 0.4:
                out.append(self.noise(sid))
            kinds = ["tool", "tool", "tool_perm", "retry"]
            if depth == 0:
                kinds += ["child", "child", "interrupt", "error"]
            else:
                kinds += ["interrupt"]
            kind = rng.choice(kinds)
            if kind == "tool":
                tool = rng.choice(TOOLS)
                call = self._id("call")
                out += [self.tool_before(sid, tool, call), self.tool_after(sid, tool, call)]
            elif kind == "tool_perm":
                tool = rng.choice(PERMISSION_TOOLS)
                call = self._id("call")
                pid = self._id("per")
                reply = rng.choice(["once", "always", "reject"])
                out += [self.tool_before(sid, tool, call), self.perm_asked(sid, tool, call, pid)]
                if rng.random() < 0.3:
                    out.append(self.noise(sid))
                out.append(self.perm_replied(sid, pid, reply))
                if reply == "reject":
                    # opencode stops the loop on a denied tool (continue_loop_on_deny
                    # is off by default) — the turn settles right after.
                    break
                out.append(self.tool_after(sid, tool, call))
            elif kind == "child":
                child = self._id("ses")
                call = self._id("call")
                out += [self.tool_before(sid, "task", call), self.created(child, parent=sid)]
                out += self.turn(child, depth=1, queued_ok=False)
                out.append(self.tool_after(sid, "task", call))
            elif kind == "retry":
                out += [self.status(sid, "retry"), self.status(sid, "busy")]
            elif kind == "interrupt":
                out += [self.error(sid, "MessageAbortedError")] + self.settle(sid)
                out += [self.message_updated(sid, assistant, "assistant"), self.status(sid, "idle"), self.idle(sid)]
                ended = True
                break
            elif kind == "error":
                out += [self.error(sid, "APIError")] + self.settle(sid)
                out += [self.message_updated(sid, assistant, "assistant"), self.status(sid, "idle"), self.idle(sid)]
                ended = True
                break
            if queued_ok and rng.random() < 0.15:
                # A second prompt typed while the turn runs (S8): opencode
                # queues it and settles once at the very end.
                m2 = self._id("msg")
                out += [self.chat_message(sid, m2), self.message_updated(sid, m2, "user")]
        if not ended:
            out.append({"hook": "experimental.text.complete", "input": {"sessionID": sid, "messageID": assistant}})
            out.append(self.message_updated(sid, assistant, "assistant"))
            out += self.settle(sid)
        out.append(self.message_updated(sid, msg, "user"))  # end-of-turn re-fire
        return out

    def stream(self):
        rng = self.rng
        out = [{"hook": "__load__", "input": {"directory": "/proj"}}]
        for _ in range(rng.randint(0, 3)):
            out.append(self.noise("-"))
        root = self._id("ses")
        resumed = rng.random() < 0.25
        if not resumed:
            out.append(self.created(root))
        for i in range(rng.randint(1, 4)):
            if i and rng.random() < 0.2:
                root = self._id("ses")
                out.append(self.created(root))  # /new
            out += self.turn(root)
            if rng.random() < 0.3:
                out.append(self.noise(root))
        return out


@pytest.fixture(scope="module")
def plugin_module(tmp_path_factory):
    target = tmp_path_factory.mktemp("plugin") / "overcode-telemetry.mjs"
    target.write_text(bundled_plugin_path().read_text(encoding="utf-8"))
    return target


def replay(plugin_module, tmp_path, streams):
    env = {
        "OVERCODE_SESSION_NAME": "oc",
        "OVERCODE_TMUX_SESSION": "agents",
        "OVERCODE_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }
    job = {"plugin": str(plugin_module), "env": env, "streams": streams}
    result = subprocess.run(
        ["node", str(REPLAY)], input=json.dumps(job),
        capture_output=True, text=True, timeout=300,
    )
    assert result.stdout, f"replay harness produced no output; stderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload.get("ok"), payload.get("error")
    return {s["name"]: s for s in payload["streams"]}


def test_generator_produces_every_construct():
    """Guard against the grammar silently degenerating."""
    seen = set()
    for seed in range(60):
        for r in StreamGen(random.Random(seed)).stream():
            if r["hook"] == "event":
                t = r["event"]["type"]
                seen.add(t)
                if t == "session.error":
                    seen.add("error:" + r["event"]["properties"]["error"]["name"])
                if t == "session.created" and "parentID" in r["event"]["properties"]["info"]:
                    seen.add("child")
                if t == "permission.replied":
                    seen.add("reply:" + r["event"]["properties"]["reply"])
                if t == "session.status":
                    seen.add("status:" + r["event"]["properties"]["status"]["type"])
            else:
                seen.add(r["hook"])
    for must in [
        "session.created", "child", "chat.message", "message.updated", "tool.execute.before",
        "tool.execute.after", "permission.asked", "reply:once", "reply:always", "reply:reject",
        "session.idle", "status:idle", "status:busy", "status:retry",
        "error:MessageAbortedError", "error:APIError", "session.updated", "message.part.updated",
    ]:
        assert must in seen, must


@node
@pytest.mark.parametrize("batch", range(4))
def test_generated_streams_match_oracle(plugin_module, tmp_path, batch):
    """100 random streams per batch, one node process each batch."""
    seeds = range(batch * 100, batch * 100 + 100)
    streams = []
    generated = {}
    for seed in seeds:
        records = StreamGen(random.Random(seed)).stream()
        generated[str(seed)] = records
        streams.append({"name": str(seed), "records": records})
    results = replay(plugin_module, tmp_path, streams)
    failures = []
    for seed, records in generated.items():
        problems = diff_publishes(records, results[seed]["publishes"])
        if problems:
            failures.append(f"seed {seed}: {problems[0]}")
        # Child ids must never be recorded as this agent's conversations.
        state = results[seed]["state"] or {}
        child_ids = {
            r["event"]["properties"]["sessionID"]
            for r in records
            if r["hook"] == "event" and r["event"]["type"] == "session.created"
            and "parentID" in r["event"]["properties"]["info"]
        }
        leaked = child_ids & set(state.get("agent_session_ids") or [])
        if leaked:
            failures.append(f"seed {seed}: child session ids leaked into agent_session_ids: {sorted(leaked)}")
    assert not failures, f"{len(failures)} of {len(seeds)} streams diverged from the oracle:\n  " + "\n  ".join(failures[:10])


@node
def test_child_permission_inside_generated_stream(plugin_module, tmp_path):
    """Find a seed whose sub-agent asks permission and pin the visible effect."""
    for seed in range(400):
        records = StreamGen(random.Random(seed)).stream()
        child_perm = [
            i for i, r in enumerate(records)
            if r["hook"] == "event" and r["event"]["type"] == "permission.asked"
            and any(
                x["hook"] == "event" and x["event"]["type"] == "session.created"
                and x["event"]["properties"]["sessionID"] == r["event"]["properties"]["sessionID"]
                and "parentID" in x["event"]["properties"]["info"]
                for x in records[:i]
            )
        ]
        if child_perm:
            break
    else:
        pytest.fail("no generated stream had a child permission ask")
    result = replay(plugin_module, tmp_path, [{"name": "c", "records": records}])["c"]
    i = child_perm[0]
    assert [p["event"] for p in result["publishes"][i]] == ["PermissionRequest"], describe(records[i])
