"""Reference semantics for opencode → overcode status telemetry (#474).

The bundled plugin (``src/overcode/opencode_plugin/overcode-telemetry.js``)
reduces opencode's plugin hooks and bus events to the hook-state events
``HookStatusDetector`` reads. This module is the *specification* of that
reduction, written independently in Python so tests can replay any event
stream — a live capture, or one generated from the grammar in
``test_opencode_event_grammar.py`` — through the real plugin and check every
publish against what the stream *should* have produced.

Stream format: the "spy" records ``tests/js/opencode_spy_plugin.js`` writes,
one dict per hook invocation::

    {"hook": "event", "event": {"type": "...", "properties": {...}}}
    {"hook": "chat.message", "input": {...}, "output": {...}}
    {"hook": "tool.execute.before", "input": {...}, "output": {...}}
    {"hook": "tool.execute.after",  "input": {...}, "output": {...}}

Rules (verified against opencode v1.18.29, Sep 2026 — see
``tests/fixtures_opencode_events/README.md`` for the captures):

* **Session scoping.** ``session.created`` without a parent id registers a
  root; with one, a child (the ``task`` tool's sub-agent). A resumed
  conversation emits no ``session.created``, so while no root is known the
  first non-child session seen is adopted. A child is never adopted, and a
  child's turn (its user message, tool calls, idle) never touches the
  parent's status — the parent is inside a ``Task`` tool call the whole time.
* **Permissions are the exception:** a child's ``permission.asked`` is
  answered in the parent's TUI, so it surfaces as ``PermissionRequest`` and
  its ``permission.replied`` puts the agent back to running.
* **Turn start.** A *new* user message (``chat.message``, or a
  ``message.updated`` with ``role: user`` and an unseen id) →
  ``UserPromptSubmit``. Re-fires for a message id already seen are ignored
  (opencode re-emits the user message at the end of the turn).
* **Tools.** ``tool.execute.before`` → ``PreToolUse``; ``tool.execute.after``
  → ``PostToolUse``. ``permission.asked`` → ``PermissionRequest``;
  ``permission.replied`` → ``PreToolUse`` (allowed) / ``PostToolUse``
  (rejected), carrying the gated tool's name.
* **Turn end.** ``session.idle`` *or* ``session.status {idle}`` → ``Stop``
  (``session.idle`` is deprecated upstream; either alone must suffice).
  Only one ``Stop`` per settle: a second idle signal while already idle
  publishes nothing.
* **Busy.** ``session.status {busy|retry}`` publishes ``UserPromptSubmit``
  only when the agent is not already running or awaiting approval — a
  safety net for a turn whose user-message hooks were missed.
* **Errors.** ``session.error`` → ``StopFailure``, and the idle that follows
  in the same breath publishes *nothing*, so the error stays visible until
  the next turn starts. ``MessageAbortedError`` is the user's Escape, not an
  error: it is ignored and the idle after it settles to ``Stop``.
* **Exit.** ``session.deleted`` → ``SessionEnd``. (``/exit`` emits no bus
  event at all; the detector's shell-prompt check covers that case.)
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

RUNNING_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"}

STATUS_OF = {
    "UserPromptSubmit": "running",
    "PreToolUse": "running",
    "PostToolUse": "running",
    "PostToolUseFailure": "running",
    "PermissionRequest": "waiting_approval",
    "Stop": "waiting_user",
    "StopFailure": "error",
    "SessionEnd": "terminated",
}

# Mirrors TOOL_NAME_MAP in the plugin.
TOOL_NAME_MAP = {
    "bash": "Bash", "edit": "Edit", "glob": "Glob", "grep": "Grep", "list": "List",
    "ls": "List", "patch": "Edit", "read": "Read", "skill": "Skill", "task": "Task",
    "todoread": "TodoRead", "todowrite": "TodoWrite", "webfetch": "WebFetch",
    "websearch": "WebSearch", "write": "Write",
}

ABORT_ERROR_NAMES = {"MessageAbortedError"}


def canonical_tool_name(name: Optional[str]) -> Optional[str]:
    if not name or not isinstance(name, str):
        return None
    mapped = TOOL_NAME_MAP.get(name.lower())
    if mapped:
        return mapped
    return name[0].upper() + name[1:]


class Publish(Tuple[str, Optional[str]]):
    """(event, tool_name) — what one plugin publish must look like."""

    def __new__(cls, event: str, tool: Optional[str] = None):
        return super().__new__(cls, (event, tool))

    @property
    def event(self) -> str:
        return self[0]

    @property
    def tool(self) -> Optional[str]:
        return self[1]


class OpencodeOracle:
    """Replays spy records and yields the publishes each one must cause."""

    def __init__(self) -> None:
        self.roots: List[str] = []
        self.children: Dict[str, str] = {}
        self.seen_messages: set = set()
        self.pending_permissions: Dict[str, Tuple[Optional[str], str]] = {}
        self.status: Optional[str] = None
        self.publishes: List[Publish] = []

    # ── scoping ──────────────────────────────────────────────────────────
    def _owns(self, sid: Optional[str], adopt: bool = False) -> bool:
        if not sid:
            return False
        if sid in self.children:
            return False
        if sid in self.roots:
            return True
        if adopt or not self.roots:
            self.roots.append(sid)
            return True
        return False

    def _root_or_child_of_root(self, sid: Optional[str]) -> bool:
        if not sid:
            return False
        if sid in self.children:
            return True  # a child of a session this process runs
        return self._owns(sid)

    # ── publish bookkeeping ──────────────────────────────────────────────
    def _publish(self, event: str, tool: Optional[str] = None) -> List[Publish]:
        self.status = STATUS_OF[event]
        p = Publish(event, tool)
        self.publishes.append(p)
        return [p]

    def _user_message(self, sid: Optional[str], message_id: Optional[str]) -> List[Publish]:
        if message_id:
            if message_id in self.seen_messages:
                return []
            self.seen_messages.add(message_id)
        if not self._owns(sid, adopt=True):
            return []
        return self._publish("UserPromptSubmit")

    def _settle(self) -> List[Publish]:
        if self.status in ("waiting_user", "error", "terminated"):
            return []
        return self._publish("Stop")

    # ── the reducer ──────────────────────────────────────────────────────
    def step(self, record: dict) -> List[Publish]:
        hook = record.get("hook")
        if hook == "event":
            return self._bus(record.get("event") or {})
        inp = record.get("input") or {}
        out = record.get("output") or {}
        if hook == "chat.message":
            msg = out.get("message") or {}
            if msg.get("role") and msg["role"] != "user":
                return []
            return self._user_message(inp.get("sessionID") or msg.get("sessionID"), msg.get("id"))
        if hook == "tool.execute.before":
            if not self._owns(inp.get("sessionID")):
                return []
            return self._publish("PreToolUse", canonical_tool_name(inp.get("tool")))
        if hook == "tool.execute.after":
            if not self._owns(inp.get("sessionID")):
                return []
            return self._publish("PostToolUse", canonical_tool_name(inp.get("tool")))
        return []

    def _bus(self, event: dict) -> List[Publish]:
        typ = event.get("type")
        props = event.get("properties") or {}
        info = props.get("info") or {}
        sid = props.get("sessionID") or info.get("sessionID") or info.get("id")

        if typ == "session.created":
            parent = info.get("parentID") or info.get("parent_id") or props.get("parentID")
            if parent:
                self.children[sid] = parent
            elif sid and sid not in self.roots:
                self.roots.append(sid)
            return []

        if typ == "message.updated":
            if info.get("role") != "user":
                return []
            return self._user_message(sid, info.get("id"))

        if typ == "permission.asked":
            if not self._root_or_child_of_root(sid):
                return []
            tool = canonical_tool_name(props.get("permission"))
            if props.get("id"):
                self.pending_permissions[props["id"]] = (tool, sid)
            return self._publish("PermissionRequest", tool)

        if typ == "permission.replied":
            if not self._root_or_child_of_root(sid):
                return []
            tool, _ = self.pending_permissions.pop(props.get("requestID"), (None, sid))
            rejected = props.get("reply") == "reject"
            return self._publish("PostToolUse" if rejected else "PreToolUse", tool)

        if typ == "session.status":
            if not self._owns(sid):
                return []
            kind = (props.get("status") or {}).get("type")
            if kind == "idle":
                return self._settle()
            if kind in ("busy", "retry"):
                if self.status in ("running", "waiting_approval"):
                    return []
                return self._publish("UserPromptSubmit")
            return []

        if typ == "session.idle":
            if not self._owns(sid):
                return []
            return self._settle()

        if typ == "session.error":
            if sid and not self._owns(sid):
                return []
            err = props.get("error") or {}
            name = err.get("name") if isinstance(err, dict) else None
            if name in ABORT_ERROR_NAMES:
                return []
            return self._publish("StopFailure")

        if typ == "session.deleted":
            if sid and not self._owns(sid):
                return []
            return self._publish("SessionEnd")

        return []

    def run(self, records: Iterable[dict]) -> List[List[Publish]]:
        return [self.step(r) for r in records]


def expected_publishes(records: Iterable[dict]) -> List[List[Publish]]:
    """Per-record list of (event, tool) publishes the plugin must make."""
    return OpencodeOracle().run(records)


def expected_status(records: Iterable[dict]) -> Optional[str]:
    """The status the agent should show once the whole stream is consumed."""
    oracle = OpencodeOracle()
    oracle.run(records)
    return oracle.status


def diff_publishes(
    records: List[dict],
    actual: List[List[dict]],
    compare_tools: bool = True,
) -> List[str]:
    """Human-readable mismatches between the oracle and the plugin's publishes.

    ``actual[i]`` is the list of ``{"event", "tool_name"}`` dicts the plugin
    appended to its event log while handling ``records[i]``.
    """
    problems: List[str] = []
    oracle = OpencodeOracle()
    for i, record in enumerate(records):
        want = oracle.step(record)
        got = actual[i] if i < len(actual) else []
        want_norm = [(p.event, p.tool if compare_tools else None) for p in want]
        got_norm = [(g.get("event"), g.get("tool_name") if compare_tools else None) for g in got]
        if want_norm != got_norm:
            problems.append(
                f"record {i} ({describe(record)}): expected {want_norm}, plugin published {got_norm}"
            )
    return problems


def describe(record: dict) -> str:
    """One-line label for a spy record, for failure messages."""
    hook = record.get("hook")
    if hook == "event":
        ev = record.get("event") or {}
        props = ev.get("properties") or {}
        info = props.get("info") or {}
        sid = props.get("sessionID") or info.get("sessionID") or info.get("id") or "-"
        extra = ""
        if ev.get("type") == "session.status":
            extra = ":" + str((props.get("status") or {}).get("type"))
        elif ev.get("type") == "message.updated":
            extra = ":" + str(info.get("role"))
        elif ev.get("type") == "permission.replied":
            extra = ":" + str(props.get("reply"))
        elif ev.get("type") == "session.error":
            err = props.get("error") or {}
            extra = ":" + str(err.get("name") if isinstance(err, dict) else err)
        return f"{ev.get('type')}{extra} sid={sid[-6:]}"
    inp = record.get("input") or {}
    sid = inp.get("sessionID") or "-"
    tool = inp.get("tool")
    return f"{hook}{(':' + tool) if tool else ''} sid={sid[-6:]}"
