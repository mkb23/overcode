"""Work a Claude agent delegates inside its own session: the 👥 rollup.

Claude Code hands work to subagents (foreground, background — the default
since 2.1.300 — and forks), workflows (whose agents report as
``workflow-subagent``) and in-process teammates. None of them is an
overcode agent; all of them report through the parent's hooks
(docs/design/agent-observability-0.6.1.md, phase 1).

Two halves, both pure (no I/O):

* **Hook side** (``apply_subagent_event`` / ``apply_parent_event``): the hook
  handler folds every event into the parent's hook state. ``subagents`` is
  the live map (agent id → kind, times, waiting/idle flags; the detector has
  read it since #507); ``swarm`` holds what has finished, the workflow runs,
  teammate names and the background-launch ids. ``Stop`` and
  ``SubagentStop`` carry ``background_tasks``, Claude's own list of the
  parent's in-flight work at that moment, and each one reconciles the map:
  whatever it no longer lists has finished.
* **Daemon side** (``rollup`` / ``in_flight``): the engine reads the state on
  its tick and publishes ``rollup`` as ``SessionDaemonState.swarm_rollup``
  (counts only, so a quiet swarm publishes nothing); the hook detector's
  armed rule reads ``in_flight`` to keep the parent yellow while delegated
  work runs.

A finished agent counts as ✅ unless something says it failed: a
``<task-notification>`` whose ``<status>`` is not ``completed`` (it reaches
the parent as a UserPromptSubmit), or a foreground Agent call's response.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

KIND_SUBAGENT = "subagent"
KIND_FORK = "fork"
KIND_WORKFLOW = "workflow"
KIND_TEAMMATE = "teammate"

# What hooks call every agent a workflow runs (no run id on the event)
WORKFLOW_AGENT_TYPE = "workflow-subagent"

RESULT_OK = "ok"
RESULT_FAILED = "failed"

# The spawning tools: Agent (Task before Claude Code renamed it), Workflow
AGENT_TOOLS = frozenset({"Agent", "Task"})
WORKFLOW_TOOL = "Workflow"

# Bounds on what the hook state keeps (design §6.1: the live set plus the
# last 200 done). Older finished agents and runs fold into counts.
DONE_KEEP = 200
# Finished members listed under an open done line (observability.done_rows)
DONE_ROWS_DEFAULT = 50
WORKFLOWS_KEEP = 50
ASYNC_KEEP = 100

# A subagent unheard from for this long is assumed gone, for hook state
# written before Claude sent ``background_tasks`` (#507). Once a Stop or
# SubagentStop has carried the list, the list decides and no timer runs.
LEGACY_STALE_SECONDS = 3600.0

# An in-process teammate's agent id is ``a<name>-<hex>`` and its agent_type
# is the name (live-verified on 2.1.300); every other agent id is hex.
_TEAMMATE_ID = re.compile(r"^a(?P<name>.+)-[0-9a-f]{8,}$")

_NOTIFICATION = re.compile(r"<task-notification>(.*?)(?:</task-notification>|$)", re.S)
_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_STATUS = re.compile(r"<status>\s*([^<\s]+)\s*</status>")
# An agent that ends its turn with a shell of its own still going gets a
# "completed" notification whose result is interim; it resumes when the
# shell ends and notifies again (2.1.301, capture 12).
_INTERIM = re.compile(r"background work of its own still running"
                      r"|waiting on its own background work")


# ---------------------------------------------------------------------------
# Hook side
# ---------------------------------------------------------------------------

def agent_kind(agent_id: str, agent_type: Optional[str], swarm: dict) -> str:
    """What kind of delegated agent ``agent_id`` is."""
    if agent_type == WORKFLOW_AGENT_TYPE:
        return KIND_WORKFLOW
    if agent_id in (swarm.get("teammates") or {}):
        return KIND_TEAMMATE
    m = _TEAMMATE_ID.match(agent_id or "")
    if m and agent_type and m.group("name") == agent_type:
        return KIND_TEAMMATE
    if agent_type == "fork":
        return KIND_FORK
    return KIND_SUBAGENT


_WORKFLOW_RUN = re.compile(r"/workflows/(?P<run>[^/]+)/agent-[^/]+$")
# A member row's "last:" line: the tool and its main argument
ACTIVITY_CHARS = 80
_ACTIVITY_ARGS = ("file_path", "path", "command", "pattern", "url", "query",
                  "description", "prompt", "skill")


def workflow_run_of(agent_transcript_path) -> Optional[str]:
    """The workflow run an agent's transcript sits under (``…/workflows/<run>/agent-x.jsonl``)."""
    if not isinstance(agent_transcript_path, str):
        return None
    m = _WORKFLOW_RUN.search(agent_transcript_path.removesuffix(".jsonl"))
    return m.group("run") if m else None


def tool_activity(tool_name, tool_input) -> Optional[str]:
    """``Read src/x.py``: what a delegated agent's tool call is doing, one short line."""
    if not isinstance(tool_name, str) or not tool_name:
        return None
    arg = None
    if isinstance(tool_input, dict):
        for key in _ACTIVITY_ARGS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                arg = " ".join(value.split())
                break
    text = f"{tool_name} {arg}" if arg else tool_name
    return text if len(text) <= ACTIVITY_CHARS else text[:ACTIVITY_CHARS - 1] + "…"


def _note_transcript(swarm: dict, data) -> None:
    """Remember the parent's transcript: its delegated agents' files sit beside it."""
    path = data.get("transcript_path") if isinstance(data, dict) else None
    if isinstance(path, str) and path.endswith(".jsonl") and swarm.get("transcript") != path:
        swarm["transcript"] = path


def task_notifications(prompt) -> list:
    """(task id, status, interim) for each ``<task-notification>`` in a prompt.

    ``interim``: the agent stopped with its own background work still
    running, so it has not finished whatever the status says.
    """
    if not isinstance(prompt, str) or "<task-notification>" not in prompt:
        return []
    out = []
    for block in _NOTIFICATION.findall(prompt):
        task = _TASK_ID.search(block)
        status = _STATUS.search(block)
        if task and status:
            out.append((task.group(1), status.group(1), bool(_INTERIM.search(block))))
    return out


# Statuses a finished task reports that mean it did not complete
_FAILED_STATUSES = frozenset({"failed", "error", "killed", "cancelled", "canceled", "stopped"})


def _result_of(status) -> Optional[str]:
    """ok / failed for a finished task's status; None for anything else."""
    if status == "completed":
        return RESULT_OK
    if status in _FAILED_STATUSES:
        return RESULT_FAILED
    return None


def _copy(subagents, swarm) -> Tuple[dict, dict]:
    subs = {k: dict(v) for k, v in (subagents or {}).items() if isinstance(v, dict)}
    sw = dict(swarm or {})
    for key in ("done", "workflows", "teammates", "folded"):
        if isinstance(sw.get(key), dict):
            sw[key] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in sw[key].items()}
    return subs, sw


def _fold(swarm: dict, key: str, result: str) -> None:
    folded = swarm.setdefault("folded", {})
    name = f"{key}_{result}"
    folded[name] = int(folded.get(name) or 0) + 1


def _finish(subagents: dict, swarm: dict, agent_id: str, result: str, now: float) -> None:
    """Move a live agent to the done map (an unknown one is recorded done)."""
    entry = subagents.pop(agent_id, None) or {}
    done = swarm.setdefault("done", {})
    done.pop(agent_id, None)  # re-inserted last: the map is oldest-first
    done[agent_id] = {"kind": entry.get("kind") or KIND_SUBAGENT, "result": result,
                      "ended_at": now}
    for key in ("run", "type", "started_at"):
        if entry.get(key):
            done[agent_id][key] = entry[key]
    while len(done) > DONE_KEEP:
        oldest = next(iter(done))
        _fold(swarm, "agents", done.pop(oldest).get("result") or RESULT_OK)


def _set_result(subagents: dict, swarm: dict, agent_id: str, result: str, now: float) -> bool:
    """Record how a non-teammate agent ended; False if it is not one we know."""
    entry = subagents.get(agent_id)
    if entry is not None and entry.get("kind") != KIND_TEAMMATE:
        _finish(subagents, swarm, agent_id, result, now)
        return True
    done = swarm.get("done") or {}
    if agent_id in done:
        done[agent_id]["result"] = result
        return True
    return False


def _waits_on_own_work(subagents: dict, swarm: dict, agent_id: str, now: float) -> None:
    """An interim notification: the agent is running again, not finished."""
    entry = subagents.get(agent_id)
    ended = (swarm.get("done") or {}).pop(agent_id, None)
    if entry is None and ended is None and agent_id not in (swarm.get("async") or []):
        return
    entry = entry or {"kind": (ended or {}).get("kind") or KIND_SUBAGENT, "started_at": now}
    entry.pop("idle", None)
    entry.update(own_work=True, last_seen=now)
    subagents[agent_id] = entry


def _end_workflow(subagents: dict, swarm: dict, task_id: str, result: str, now: float) -> None:
    run = (swarm.get("workflows") or {}).get(task_id)
    if run is None:
        return
    if run.get("status") == "running":
        run["ended_at"] = now
    run["status"] = result
    _end_orphan_workflow_agents(subagents, swarm, now)


def _running_workflows(swarm: dict) -> int:
    return sum(1 for r in (swarm.get("workflows") or {}).values()
               if isinstance(r, dict) and r.get("status") == "running")


def _end_orphan_workflow_agents(subagents: dict, swarm: dict, now: float) -> None:
    """With no workflow left running, none of its agents is either."""
    if _running_workflows(swarm):
        return
    for agent_id in [a for a, e in subagents.items() if e.get("kind") == KIND_WORKFLOW]:
        _finish(subagents, swarm, agent_id, RESULT_OK, now)


def _add_workflow(swarm: dict, task_id: str, name=None, run_id=None, now=None) -> None:
    runs = swarm.setdefault("workflows", {})
    run = runs.get(task_id) or {}
    run["status"] = run.get("status") or "running"
    if run.get("started_at") is None and now is not None:
        run["started_at"] = now
    if name and not run.get("name"):
        run["name"] = str(name)[:80]
    if run_id and not run.get("run_id"):
        run["run_id"] = str(run_id)
    runs[task_id] = run
    while len(runs) > WORKFLOWS_KEEP:
        oldest = next((k for k, r in runs.items() if r.get("status") != "running"), None)
        if oldest is None:
            break
        _fold(swarm, "workflows", runs.pop(oldest).get("status") or RESULT_OK)


def _note_async(swarm: dict, agent_id: str) -> None:
    ids = [a for a in (swarm.get("async") or []) if a != agent_id]
    ids.append(agent_id)
    swarm["async"] = ids[-ASYNC_KEEP:]


def _reconcile(subagents: dict, swarm: dict, tasks, now: float,
               turn_over: bool, exclude: Optional[str] = None) -> None:
    """Fold Claude's in-flight list (``background_tasks``) into the map.

    ``turn_over``: the parent's own Stop, when no foreground call can still
    be running, so every subagent the list leaves out has finished. On a
    SubagentStop (``exclude`` is the stopping agent, which the list still
    shows) only work that is always in the background is reconciled:
    workflows, teammates and background-launched subagents.
    """
    running = [t for t in tasks if isinstance(t, dict) and t.get("id")
               and t.get("id") != exclude and t.get("status", "running") == "running"]
    ids = {t["id"] for t in running}
    swarm["bg_at"] = now

    for t in running:
        if t.get("type") == "workflow":
            _add_workflow(swarm, t["id"], name=t.get("name") or t.get("description"), now=now)
    for task_id, run in (swarm.get("workflows") or {}).items():
        if run.get("status") == "running" and task_id not in ids:
            run["status"] = RESULT_OK
            run["ended_at"] = now

    shells = any(t.get("type") == "shell" for t in running)
    team_descs = {t.get("description") for t in running if t.get("type") == "teammate"}
    teammates = swarm.get("teammates") or {}
    async_ids = set(swarm.get("async") or [])
    for agent_id, entry in list(subagents.items()):
        kind = entry.get("kind") or KIND_SUBAGENT
        if kind == KIND_WORKFLOW:
            continue  # by run, below
        if kind == KIND_TEAMMATE:
            desc = (teammates.get(agent_id) or {}).get("desc")
            gone = (desc not in team_descs) if desc else not team_descs
        else:
            gone = agent_id not in ids and (turn_over or agent_id in async_ids)
            # Claude lists the shell it waits on, not the agent itself
            gone = gone and not (entry.get("own_work") and shells)
        if gone:
            _finish(subagents, swarm, agent_id, RESULT_OK, now)
    _end_orphan_workflow_agents(subagents, swarm, now)

    # In flight but never seen starting (its SubagentStart was lost), or
    # seen stopping while Claude still runs it: a subagent that ends its
    # turn with its own background work going (a shell) fires SubagentStop,
    # and Claude reports it "waiting on its own background work" in this
    # list until that work ends. It is running again, not finished.
    done = swarm.get("done") or {}
    for t in running:
        if t.get("type") != "subagent" or t["id"] in subagents:
            continue
        ended = done.pop(t["id"], None)
        agent_type = t.get("agent_type")
        subagents[t["id"]] = {
            "type": agent_type,
            "kind": (ended or {}).get("kind") or agent_kind(t["id"], agent_type, swarm),
            "started_at": now, "last_seen": now,
        }


def apply_subagent_event(subagents, swarm, event: str, agent_id: str,
                         data: dict, now: float) -> Tuple[dict, dict]:
    """The live map and swarm after one of the parent's agents' own events."""
    subagents, swarm = _copy(subagents, swarm)
    entry = subagents.get(agent_id)
    if event == "SubagentStart":
        agent_type = data.get("agent_type")
        (swarm.get("done") or {}).pop(agent_id, None)  # resumed: counted once
        subagents[agent_id] = {
            "type": agent_type,
            "kind": (entry or {}).get("kind") or agent_kind(agent_id, agent_type, swarm),
            "started_at": (entry or {}).get("started_at", now),
            "last_seen": now,
        }
    elif event == "SubagentStop":
        run = workflow_run_of(data.get("agent_transcript_path"))
        if run and entry is not None:
            entry["run"] = run
        if entry is not None and entry.get("kind") == KIND_TEAMMATE:
            # A teammate stops each time it goes idle; it is still listening
            entry.pop("waiting", None)
            entry.update(idle=True, last_seen=now)
        elif entry is not None or agent_id in (swarm.get("async") or []):
            if entry is None:
                subagents[agent_id] = {"kind": agent_kind(agent_id, data.get("agent_type"), swarm)}
            _finish(subagents, swarm, agent_id, RESULT_OK, now)
        # else: a stop for an agent never seen starting is one of Claude's
        # own helpers (the away summary, auto-compaction), which fire
        # SubagentStop with no SubagentStart and write no transcript: not
        # delegated work. A real agent whose start was lost is still seen
        # through its Agent call's response, or as async-launched.
        tasks = data.get("background_tasks")
        if isinstance(tasks, list):
            _reconcile(subagents, swarm, tasks, now, turn_over=False, exclude=agent_id)
    elif event == "TeammateIdle":
        if entry is not None:
            entry.pop("waiting", None)
            entry.update(idle=True, last_seen=now)
    elif entry is not None:
        entry["last_seen"] = now
        entry.pop("idle", None)
        if event == "PermissionRequest":
            entry["waiting"] = True
        else:
            entry.pop("waiting", None)
        if event == "PreToolUse":
            activity = tool_activity(data.get("tool_name"), data.get("tool_input"))
            if activity:
                entry["activity"] = activity
    _note_transcript(swarm, data)
    return subagents, swarm


def _on_agent_call(subagents: dict, swarm: dict, tool_input, response, now: float) -> None:
    if not isinstance(response, dict):
        return
    agent_id = response.get("agentId") or response.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return
    status = response.get("status")
    if status == "async_launched":
        _note_async(swarm, agent_id)
    elif status == "teammate_spawned":
        desc = tool_input.get("description") if isinstance(tool_input, dict) else None
        name = response.get("name") or (tool_input or {}).get("name")
        swarm.setdefault("teammates", {})[agent_id] = {"name": name, "desc": desc}
        if agent_id in subagents:
            subagents[agent_id]["kind"] = KIND_TEAMMATE
    elif _result_of(status) is not None:
        # A foreground call returned: its agent is done (SubagentStop
        # usually said so already), and the response says how it went
        result = _result_of(status)
        if not _set_result(subagents, swarm, agent_id, result, now):
            _finish(subagents, swarm, agent_id, result, now)


def apply_parent_event(subagents, swarm, event: str, tool_name: Optional[str],
                       tool_input, data: Optional[dict], now: float) -> Tuple[dict, dict]:
    """The live map and swarm after one of the parent's own events.

    SessionEnd (an exit, or a /clear) drops it all: the conversation the
    swarm belonged to is over.
    """
    if event == "SessionEnd":
        return {}, {}
    subagents, swarm = _copy(subagents, swarm)
    data = data or {}
    if event == "PostToolUse" and tool_name in AGENT_TOOLS:
        _on_agent_call(subagents, swarm, tool_input, data.get("tool_response"), now)
    elif event == "PostToolUse" and tool_name == WORKFLOW_TOOL:
        response = data.get("tool_response")
        if isinstance(response, dict) and response.get("taskId"):
            _add_workflow(swarm, response["taskId"], name=response.get("workflowName"),
                          run_id=response.get("runId"), now=now)
    elif event == "UserPromptSubmit":
        for task_id, status, interim in task_notifications(data.get("prompt")):
            # A notification only comes once a task has ended, or paused
            # on its own background work
            result = _result_of(status) or RESULT_FAILED
            if interim and result == RESULT_OK and task_id not in (swarm.get("workflows") or {}):
                _waits_on_own_work(subagents, swarm, task_id, now)
            elif task_id in (swarm.get("workflows") or {}):
                _end_workflow(subagents, swarm, task_id, result, now)
            else:
                _set_result(subagents, swarm, task_id, result, now)
    if event == "Stop" and isinstance(data.get("background_tasks"), list):
        _reconcile(subagents, swarm, data["background_tasks"], now, turn_over=True)
    if subagents or swarm:
        _note_transcript(swarm, data)
    return subagents, swarm


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------

def _live_items(hook_state: dict, now: float) -> Tuple[list, int]:
    """The live (agent id, entry) pairs, and how many legacy entries went stale."""
    subagents = hook_state.get("subagents")
    if not isinstance(subagents, dict):
        return [], 0
    swarm = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    reconciled = "bg_at" in swarm
    live, stale = [], 0
    for agent_id, entry in subagents.items():
        if not isinstance(entry, dict):
            continue
        if not reconciled:
            seen = entry.get("last_seen")
            if not isinstance(seen, (int, float)) or now - seen > LEGACY_STALE_SECONDS:
                stale += 1
                continue
        live.append((agent_id, entry))
    return live, stale


def _live(hook_state: dict, now: float) -> Tuple[list, int]:
    """The live entries, and how many legacy entries went stale."""
    items, stale = _live_items(hook_state, now)
    return [entry for _, entry in items], stale


def in_flight(hook_state: dict, now: float) -> Tuple[int, int]:
    """(agents, workflows) the parent is still waiting on.

    Idle teammates wait for a message, not on anything: they don't count.
    A workflow counts once, however many agents it runs; its agents count
    only when no run is known (the Workflow call went unseen).
    """
    live, _ = _live(hook_state, now)
    swarm = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    workflows = _running_workflows(swarm)
    agents = 0
    for entry in live:
        if entry.get("idle"):
            continue
        if entry.get("kind") == KIND_WORKFLOW and workflows:
            continue
        agents += 1
    return agents, workflows


def _count(values: Iterable, result: str) -> int:
    return sum(1 for v in values if isinstance(v, dict) and v.get("result") == result)


def rollup(hook_state: Optional[dict], now: float) -> Optional[dict]:
    """The 👥 cell's counts, or None when the agent never delegated.

    ``total`` agents (live and finished; a workflow's agents included),
    ``running``, ``waiting`` (a permission prompt), ``idle`` (teammates
    listening), ``ok`` and ``failed``; ``workflows`` runs seen and
    ``workflows_running``. Counts only: the value holds still until an
    event changes it.
    """
    if not isinstance(hook_state, dict):
        return None
    live, stale = _live(hook_state, now)
    swarm = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    done = (swarm.get("done") or {}).values()
    folded = swarm.get("folded") or {}
    running = waiting = idle = 0
    for entry in live:
        if entry.get("waiting"):
            waiting += 1
        elif entry.get("idle"):
            idle += 1
        else:
            running += 1
    ok = _count(done, RESULT_OK) + int(folded.get("agents_ok") or 0) + stale
    failed = _count(done, RESULT_FAILED) + int(folded.get("agents_failed") or 0)
    runs = [r for r in (swarm.get("workflows") or {}).values() if isinstance(r, dict)]
    workflows = len(runs) + int(folded.get("workflows_ok") or 0) + int(
        folded.get("workflows_failed") or 0)
    total = len(live) + ok + failed
    if not total and not workflows:
        return None
    return {
        "total": total, "running": running, "waiting": waiting, "idle": idle,
        "ok": ok, "failed": failed, "workflows": workflows,
        "workflows_running": sum(1 for r in runs if r.get("status") == "running"),
    }


# ---------------------------------------------------------------------------
# Virtual rows (phase 2): an expanded parent's delegated work, row by row
# ---------------------------------------------------------------------------
#
# Published only for parents a visible view has expanded (the engine's
# ``swarm:<id>`` subscription), in the snapshot's ``virtual`` section, keyed
# by the row's identity (design §3.2):
#
#   <parent>/wf/<run>             a workflow run (group)
#   <parent>/wf/<run>/ph/<n>      one of its phases
#   <parent>/wf/<run>/<agent id>  one of its agents
#   <parent>/tm, <parent>/tm/<name>     the team and a teammate
#   <parent>/sa, <parent>/sa/<agent id> loose subagents (and forks)
#
# Every value holds still between events: absolute times, counts, names.
# A finished member keeps its row for ``keep_seconds`` after it ended (so
# does a finished workflow run's group); the earliest such deadline is
# returned with the rows, for the engine to publish their removal on time.
# A group whose done line a view opened (subscription ``<group>/done``)
# also lists its most recently finished members, up to ``done_limit``;
# that list moves only when a member finishes, never with the clock.

ROW_GROUP = "group"
ROW_PHASE = "phase"
ROW_MEMBER = "member"

GROUP_WORKFLOW = "workflow"
GROUP_TEAM = "team"
GROUP_AGENTS = "agents"

LIGHT_GREEN = "green"     # working
LIGHT_YELLOW = "yellow"   # waiting on its own background work
LIGHT_ORANGE = "orange"   # its permission prompt is up in the parent's pane
LIGHT_IDLE = "idle"       # a teammate, listening
LIGHT_DONE = "done"       # finished: see ``result``

RESULT_TEXT_CHARS = 80
_COUNT_KEYS = ("running", "yellow", "waiting", "idle", "ok", "failed")
_LIGHT_COUNT = {LIGHT_GREEN: "running", LIGHT_YELLOW: "yellow", LIGHT_ORANGE: "waiting",
                LIGHT_IDLE: "idle"}


class NoDetails:
    """Lookups into the transcript directory (``swarm_files.SwarmFiles``); none here."""

    def meta(self, agent_id: str, run_id: Optional[str]) -> Optional[dict]:
        return None

    def journal(self, run_id: str) -> Optional[list]:
        return None


def member_light(entry: dict) -> str:
    """A live member's light (design §3.4)."""
    if entry.get("waiting"):
        return LIGHT_ORANGE
    if entry.get("idle"):
        return LIGHT_IDLE
    if entry.get("own_work"):
        return LIGHT_YELLOW
    return LIGHT_GREEN


def group_light(counts: dict) -> str:
    """A group's light: any 🟠, else any 🟢, else 🟡, else idle, else done."""
    if counts.get("waiting"):
        return LIGHT_ORANGE
    if counts.get("running"):
        return LIGHT_GREEN
    if counts.get("yellow"):
        return LIGHT_YELLOW
    if counts.get("idle"):
        return LIGHT_IDLE
    return LIGHT_DONE


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _cost(value: float) -> Optional[float]:
    return round(value, 4) if value > 0 else None


def spend_by_agent(conversations) -> Tuple[dict, dict]:
    """``({agent id: (cost, tokens)}, {workflow run: cost})`` from an agent's
    spend per conversation (``SessionStats.conversations``: a subagent is
    ``<session>/agent-<id>``, a workflow agent ``<session>/workflows/<run>/agent-<id>``)."""
    agents: dict = {}
    runs: dict = {}
    if not isinstance(conversations, dict):
        return agents, runs
    for key, entry in conversations.items():
        if not isinstance(key, str) or key.startswith("_") or "/" not in key:
            continue
        if not isinstance(entry, dict):
            continue
        parts = key.split("/")[1:]
        name = parts[-1]
        if not name.startswith("agent-"):
            continue
        cost = _num(entry.get("cost"))
        tokens = int(sum(_num(entry.get(k)) for k in ("in", "out", "cw", "cr")))
        agent_id = name[len("agent-"):]
        old_cost, old_tokens = agents.get(agent_id, (0.0, 0))
        agents[agent_id] = (old_cost + cost, old_tokens + tokens)
        if len(parts) >= 3 and parts[0] == "workflows":
            runs[parts[1]] = runs.get(parts[1], 0.0) + cost
    return agents, runs


def subagent_cost(conversations) -> Optional[float]:
    """What the agent's delegated work cost (already inside its own total), or None."""
    agents, _ = spend_by_agent(conversations)
    return _cost(sum(cost for cost, _ in agents.values()))


def parse_journal(events) -> dict:
    """A workflow journal (``started``/``result`` lines) as
    ``{"agents": {id: {"label", "phase", "result"}}, "phases": [title, ...]}``."""
    agents: dict = {}
    phases: list = []
    for event in events or ():
        if not isinstance(event, dict):
            continue
        agent_id = event.get("agentId")
        if not isinstance(agent_id, str):
            continue
        if event.get("type") == "started":
            phase = event.get("phase") if isinstance(event.get("phase"), str) else None
            agents[agent_id] = {"label": event.get("label"), "phase": phase, "result": None}
            if phase and phase not in phases:
                phases.append(phase)
        elif event.get("type") == "result" and agent_id in agents:
            result = event.get("result")
            agents[agent_id]["result"] = result if isinstance(result, str) else "done"
    return {"agents": agents, "phases": phases}


def _short(text) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    text = " ".join(text.split())
    return text if len(text) <= RESULT_TEXT_CHARS else text[:RESULT_TEXT_CHARS - 1] + "…"


class _Builder:
    """One parent's virtual rows (``virtual_rows``)."""

    def __init__(self, parent, hook_state, now, keep_seconds, details, conversations,
                 done_open=(), done_limit=DONE_ROWS_DEFAULT):
        self.parent = parent
        self.now = now
        self.keep = keep_seconds
        self.details = details
        # Groups whose done line is open: their finished members get rows too,
        # the most recent ``done_limit`` of them (counting those still kept)
        self.done_open = frozenset(done_open or ())
        self.done_limit = max(0, int(done_limit))
        self.done_kept: dict = {}        # group key -> finished members shown anyway
        self.done_waiting: dict = {}     # group key -> [(ended, order, agent id, entry)]
        swarm = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
        live, _ = _live_items(hook_state, now)
        done = [(a, e) for a, e in (swarm.get("done") or {}).items() if isinstance(e, dict)]
        self.members = [(a, e, True) for a, e in live] + [(a, e, False) for a, e in done]
        self.done_order = {a: i for i, (a, _) in enumerate(done)}  # the map is oldest-first
        self.runs = {t: r for t, r in (swarm.get("workflows") or {}).items()
                     if isinstance(r, dict)}
        self.teammates = swarm.get("teammates") or {}
        self.spend, self.run_spend = spend_by_agent(conversations)
        self.rows: dict = {}
        self.groups: dict = {}
        self.deadlines: list = []
        # Workflow runs: their journals say which agents are theirs
        self.journals: dict = {}
        self.run_of_agent: dict = {}
        self.task_of_run: dict = {}
        for task_id, run in self.runs.items():
            run_id = run.get("run_id")
            if not run_id:
                continue
            self.task_of_run[run_id] = task_id
            journal = parse_journal(details.journal(run_id))
            self.journals[task_id] = journal
            for agent_id in journal["agents"]:
                self.run_of_agent[agent_id] = task_id
        self.running_runs = [t for t, r in self.runs.items() if r.get("status") == "running"]
        self.run_order = {t: i for i, t in enumerate(self.runs)}
        self.shown_runs = {t: self._run_shown(r) for t, r in self.runs.items()}

    # -- workflow runs ----------------------------------------------------

    def _kept(self, ended) -> bool:
        """A finished thing still within its time on screen (noting the deadline)."""
        if not isinstance(ended, (int, float)) or self.now >= ended + self.keep:
            return False
        self.deadlines.append(ended + self.keep)
        return True

    def _run_shown(self, run: dict) -> bool:
        return run.get("status") == "running" or self._kept(run.get("ended_at"))

    def _run_for(self, agent_id: str, entry: dict, is_live: bool) -> Optional[str]:
        task = self.run_of_agent.get(agent_id) or self.task_of_run.get(entry.get("run"))
        if task:
            return task
        if is_live and self.running_runs:
            return self.running_runs[-1]
        if len(self.runs) == 1:
            return next(iter(self.runs))
        return None

    def _run_key(self, task_id: Optional[str]) -> str:
        run_id = (self.runs.get(task_id) or {}).get("run_id")
        return f"{self.parent}/wf/{run_id or task_id or '-'}"

    def _run_group(self, task_id: Optional[str]) -> dict:
        run = self.runs.get(task_id) or {}
        return self._group(self._run_key(task_id), GROUP_WORKFLOW,
                           self.run_order.get(task_id, len(self.runs)),
                           title=run.get("name") or "workflow", run_id=run.get("run_id"),
                           status=run.get("status") or "running",
                           started_at=run.get("started_at"))

    # -- groups and members -----------------------------------------------

    def _group(self, key: str, kind: str, order: int, **fields) -> dict:
        row = self.groups.get(key)
        if row is None:
            row = {"parent": self.parent, "row": ROW_GROUP, "kind": kind, "order": order,
                   "counts": {k: 0 for k in _COUNT_KEYS}}
            row.update({k: v for k, v in fields.items() if v is not None})
            self.groups[key] = row
        return row

    def _place(self, agent_id: str, entry: dict, is_live: bool):
        """(group row, its key, the member's key, shown?, workflow task)."""
        kind = entry.get("kind") or KIND_SUBAGENT
        if kind == KIND_WORKFLOW:
            task = self._run_for(agent_id, entry, is_live)
            shown = self.shown_runs.get(task, False) if task is not None else is_live
            key = self._run_key(task)
            return self._run_group(task), key, f"{key}/{agent_id}", shown, task
        if kind == KIND_TEAMMATE:
            key = f"{self.parent}/tm"
            name = (self.teammates.get(agent_id) or {}).get("name") or entry.get("type") or agent_id
            return self._group(key, GROUP_TEAM, 1000, title="team"), key, f"{key}/{name}", True, None
        key = f"{self.parent}/sa"
        return self._group(key, GROUP_AGENTS, 2000, title="agents"), key, f"{key}/{agent_id}", True, None

    def _member(self, agent_id, entry, is_live) -> None:
        group, gkey, mkey, shown, task = self._place(agent_id, entry, is_live)
        counts = group["counts"]
        if is_live:
            light, result, ended = member_light(entry), None, None
            counts[_LIGHT_COUNT[light]] += 1
        else:
            light, result = LIGHT_DONE, entry.get("result") or RESULT_OK
            ended = entry.get("ended_at")
            counts["failed" if result == RESULT_FAILED else "ok"] += 1
        if not shown:
            return
        if not is_live:
            if self._kept(ended):
                self.done_kept[gkey] = self.done_kept.get(gkey, 0) + 1
            else:
                if gkey in self.done_open:
                    stamp = ended if isinstance(ended, (int, float)) else 0
                    self.done_waiting.setdefault(gkey, []).append(
                        (stamp, self.done_order.get(agent_id, 0), agent_id, entry))
                return
        self._emit(agent_id, entry, mkey, gkey, task, light, result, ended)

    def _listed_done(self) -> None:
        """Open done lines: their most recent finished members, up to the limit."""
        for gkey, waiting in self.done_waiting.items():
            room = self.done_limit - self.done_kept.get(gkey, 0)
            if room <= 0:
                continue
            waiting.sort(key=lambda w: (w[0], w[1]), reverse=True)
            for ended, _, agent_id, entry in waiting[:room]:
                _, _, mkey, _, task = self._place(agent_id, entry, False)
                self._emit(agent_id, entry, mkey, gkey, task, LIGHT_DONE,
                           entry.get("result") or RESULT_OK, entry.get("ended_at"))

    def _emit(self, agent_id, entry, mkey, gkey, task, light, result, ended) -> None:
        run = self.runs.get(task) or {}
        journal = self.journals.get(task) or {"agents": {}, "phases": []}
        from_journal = journal["agents"].get(agent_id) or {}
        meta = self.details.meta(agent_id, run.get("run_id") or entry.get("run")) or {}
        kind = entry.get("kind") or KIND_SUBAGENT
        title = (from_journal.get("label") or meta.get("description")
                 or (mkey.rsplit("/", 1)[1] if kind == KIND_TEAMMATE else None)
                 or entry.get("type") or meta.get("agentType") or agent_id)
        model = meta.get("model")
        cost, tokens = self.spend.get(agent_id, (0.0, 0))
        phase = from_journal.get("phase") or meta.get("workflowPhase")
        row = {
            "parent": self.parent, "row": ROW_MEMBER, "group": gkey, "kind": kind,
            "agent_id": agent_id, "title": str(title)[:80], "light": light,
            "agent_type": entry.get("type") or meta.get("agentType"),
            "started_at": entry.get("started_at"), "ended_at": ended, "result": result,
            "activity": entry.get("activity"),
            "model": model if isinstance(model, str) and model != "inherit" else None,
            "cost_usd": _cost(cost), "tokens": tokens or None,
            "result_text": _short(from_journal.get("result")),
        }
        if phase in journal["phases"]:
            row["phase"] = f"{gkey}/ph/{journal['phases'].index(phase)}"
        self.rows[mkey] = {k: v for k, v in row.items() if v is not None}

    def _finish_workflow(self, key: str, row: dict) -> None:
        task = next((t for t in self.runs if self._run_key(t) == key), None)
        run_id = row.get("run_id")
        if run_id and self.run_spend.get(run_id):
            row["cost_usd"] = _cost(self.run_spend[run_id])
        journal = self.journals.get(task)
        if not journal or not journal["agents"]:
            return
        row["agents"] = len(journal["agents"])
        for i, title in enumerate(journal["phases"]):
            in_phase = [a for a in journal["agents"].values() if a["phase"] == title]
            finished = sum(1 for a in in_phase if a["result"] is not None)
            self.rows[f"{key}/ph/{i}"] = {
                "parent": self.parent, "row": ROW_PHASE, "group": key, "title": title,
                "order": i, "started": len(in_phase), "done": finished,
            }
            row["phase"], row["phase_done"], row["phase_started"] = title, finished, len(in_phase)

    def build(self) -> Tuple[dict, Optional[float]]:
        for agent_id, entry, is_live in self.members:
            self._member(agent_id, entry, is_live)
        self._listed_done()
        # A run whose agents have not started yet still gets its group
        for task_id in self.runs:
            if self.shown_runs[task_id]:
                self._run_group(task_id)
        for key, row in self.groups.items():
            counts = row["counts"]
            if row["kind"] == GROUP_WORKFLOW:
                task = next((t for t in self.runs if self._run_key(t) == key), None)
                if task is not None and not self.shown_runs[task]:
                    continue
                if task is None and not any(counts.get(k) for k in _LIGHT_COUNT.values()):
                    continue
                self._finish_workflow(key, row)
            else:
                team = row["kind"] == GROUP_TEAM
                total = sum(self.spend.get(a, (0.0, 0))[0] for a, e, _ in self.members
                            if e.get("kind") != KIND_WORKFLOW
                            and (e.get("kind") == KIND_TEAMMATE) == team)
                if total:
                    row["cost_usd"] = _cost(total)
            row["light"] = group_light(counts)
            row["counts"] = {k: v for k, v in counts.items() if v}
            self.rows[key] = row
        return self.rows, (min(self.deadlines) if self.deadlines else None)


def virtual_rows(parent: str, hook_state: Optional[dict], now: float, keep_seconds: float,
                 details=None, conversations=None, done_open=(),
                 done_limit: int = DONE_ROWS_DEFAULT) -> Tuple[dict, Optional[float]]:
    """``parent``'s virtual rows and when they next change with the clock (None: never).

    ``details`` reads members' ``.meta.json`` and workflow journals
    (``NoDetails`` when absent); ``conversations`` is the parent's spend
    per conversation, for each row's own share of it. ``done_open`` names
    the groups whose done line a view opened: their ``done_limit`` most
    recently finished members get rows (beyond ``keep_seconds`` too); the
    rest stay counts. Pure otherwise.
    """
    if not isinstance(hook_state, dict):
        return {}, None
    builder = _Builder(parent, hook_state, now, keep_seconds,
                       details if details is not None else NoDetails(), conversations,
                       done_open, done_limit)
    return builder.build()
