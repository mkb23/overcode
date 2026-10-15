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


def task_notifications(prompt) -> list:
    """(task id, status) for each ``<task-notification>`` in a prompt."""
    if not isinstance(prompt, str) or "<task-notification>" not in prompt:
        return []
    out = []
    for block in _NOTIFICATION.findall(prompt):
        task = _TASK_ID.search(block)
        status = _STATUS.search(block)
        if task and status:
            out.append((task.group(1), status.group(1)))
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


def _end_workflow(subagents: dict, swarm: dict, task_id: str, result: str, now: float) -> None:
    run = (swarm.get("workflows") or {}).get(task_id)
    if run is None:
        return
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


def _add_workflow(swarm: dict, task_id: str, name=None, run_id=None) -> None:
    runs = swarm.setdefault("workflows", {})
    run = runs.get(task_id) or {}
    run["status"] = run.get("status") or "running"
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
            _add_workflow(swarm, t["id"], name=t.get("name") or t.get("description"))
    for task_id, run in (swarm.get("workflows") or {}).items():
        if run.get("status") == "running" and task_id not in ids:
            run["status"] = RESULT_OK

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
        if gone:
            _finish(subagents, swarm, agent_id, RESULT_OK, now)
    _end_orphan_workflow_agents(subagents, swarm, now)

    # In flight but never seen starting (its SubagentStart was lost)
    done = swarm.get("done") or {}
    for t in running:
        if t.get("type") == "subagent" and t["id"] not in subagents and t["id"] not in done:
            agent_type = t.get("agent_type")
            subagents[t["id"]] = {
                "type": agent_type, "kind": agent_kind(t["id"], agent_type, swarm),
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
                          run_id=response.get("runId"))
    elif event == "UserPromptSubmit":
        for task_id, status in task_notifications(data.get("prompt")):
            # A notification only comes once a task has ended
            result = _result_of(status) or RESULT_FAILED
            if task_id in (swarm.get("workflows") or {}):
                _end_workflow(subagents, swarm, task_id, result, now)
            else:
                _set_result(subagents, swarm, task_id, result, now)
    if event == "Stop" and isinstance(data.get("background_tasks"), list):
        _reconcile(subagents, swarm, data["background_tasks"], now, turn_over=True)
    return subagents, swarm


# ---------------------------------------------------------------------------
# Daemon side
# ---------------------------------------------------------------------------

def _live(hook_state: dict, now: float) -> Tuple[list, int]:
    """The live entries, and how many legacy entries went stale."""
    subagents = hook_state.get("subagents")
    if not isinstance(subagents, dict):
        return [], 0
    swarm = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    reconciled = "bg_at" in swarm
    live, stale = [], 0
    for entry in subagents.values():
        if not isinstance(entry, dict):
            continue
        if not reconciled:
            seen = entry.get("last_seen")
            if not isinstance(seen, (int, float)) or now - seen > LEGACY_STALE_SECONDS:
                stale += 1
                continue
        live.append(entry)
    return live, stale


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
