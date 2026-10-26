"""Background shells: services, batch work and watchers (#515).

docs/design/background-shell-classification.md. An agent at the prompt with
background shells running is yellow (#507), but a dev server, a build and a
poll loop mean different things for "will this agent pick itself back up?".

Where the shells come from:

* **Claude's own list.** Every parent ``Stop`` carries ``background_tasks``;
  its ``type: shell`` entries (id, command, description) are the shells
  still running as the turn ends. The hook side keeps them in the swarm
  state as ``bg_shells`` (``swarm.note_bg_shells``). A shell that ends
  later wakes the agent with a task notification, which drops it, and the
  next ``Stop`` lists what is left.
* **Its output file.** ``tasks/<id>.output`` gets an exit mark when the
  shell ends (``swarm_files.ShellEnds``): an ended shell stops counting
  even before the agent wakes.
* **The process table.** Claude runs each one as
  ``zsh -c source …/shell-snapshots/… && eval '<command>' < /dev/null && pwd -P >| …``
  under the agent's CLI (through ``claude bg-pty-host`` / ``bg-spare``
  helpers). Matching the eval'd command gives the pid, which the kill
  command needs, and runtime signals: a ``sleep`` child (a poll loop) and
  a listening socket (a service).

Classification is rules on the command and Claude's description, then the
runtime signals. A **watcher** whose target (an overcode agent or job) has
ended while the watcher still polls is **stuck**: the warning badge the
owner chose (2026-10-06), never a colour change and never a kill.

Everything here is pure; the daemon's ``ShellProbe`` does the I/O.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

KIND_SERVICE = "service"
KIND_BATCH = "batch"
KIND_WATCHER = "watcher"

# Badge kind (status_constants.BADGE_KINDS) for each shell kind
BADGE_FOR_KIND = {
    KIND_SERVICE: "bg_service",
    KIND_BATCH: "bg_batch",
    KIND_WATCHER: "bg_watcher",
}
STUCK_BADGE = "bg_stuck"

# A watcher still polling this long after its target ended is stuck: long
# enough for any sane poll interval to notice, short enough to matter
STUCK_GRACE_SECONDS = 90.0
# Commands are published cut to this
COMMAND_CHARS = 200

# The marker of a Bash tool shell, and its eval'd command
_SNAPSHOT_MARK = "/shell-snapshots/"
_EVAL = re.compile(r"&& eval (?='|\")")

# --- rules ---------------------------------------------------------------

_I = re.IGNORECASE
# Watchers whose target overcode can check, and the tools that end on their own
_OVERCODE_FOLLOW = re.compile(r"\boverc(?:ode)?\s+follow\b(?P<rest>.*)", _I)
_OVERCODE_JOB = re.compile(r"\boverc(?:ode)?\s+jobs\s+(?P<verb>tail|wait|attach)\b(?P<rest>.*)", _I)
_WATCH_TOOLS = re.compile(
    r"\bgh\s+run\s+watch\b|\bgh\s+pr\s+checks\b.*--watch\b|\bkubectl\s+(?:wait|rollout\s+status)\b"
    r"|\bdocker\s+wait\b|\bwait-for(?:-it)?\b", _I)
# A loop that sleeps between checks
_POLL_LOOP = re.compile(r"\b(?:until|while)\b.*\bsleep\b", _I | re.S)
# `sleep N && check`: a one-shot delayed check
_DELAYED_CHECK = re.compile(r"^\s*sleep\s+\d+(?:\.\d+)?[smh]?\s*(?:&&|;)", _I)
# tail -f piped into a grep that stops at the first match
_TAIL_UNTIL = re.compile(r"\btail\s+-[a-zA-Z]*[fF]\b.*\|\s*grep\b[^|]*\s-[a-zA-Z]*[qm]", _I | re.S)

_SERVICES = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|preview|watch)\b"
    r"|\b(?:vite|nodemon|live-server|http-server|uvicorn|gunicorn|hypercorn|daphne|ngrok"
    r"|redis-server|mongod|caffeinate)\b"
    r"|\bnext\s+(?:dev|start)\b|\bnuxt\s+dev\b|\bwebpack(?:-dev-server|\s+serve)\b"
    r"|\bflask\s+run\b|\brails\s+s(?:erver)?\b|\bmanage\.py\s+runserver\b|\bphp\s+-S\b"
    r"|\bhttp\.server\b|\bSimpleHTTPServer\b|\b(?:jekyll|hugo|mkdocs)\s+serve\b|\bhugo\s+server\b"
    r"|\bserve\s+(?:-|\.|/)|\bserve\s*$"
    r"|\btail\s+-[a-zA-Z]*[fF]\b|\s--watch\b|\s-w\s*$|\s--forever\b"
    r"|\bdocker(?:-|\s+)compose\s+up\b(?![^;&|]*\s-d\b)"
    r"|\bcargo\s+watch\b|\bwatchexec\b|\bentr\b", _I)

_DESC_WATCHER = re.compile(r"\b(?:wait(?:ing)?\s+(?:for|on|until)|poll(?:ing)?|watch(?:ing)?\s+(?:for|until|the|ci)|until\s+.*\b(?:done|finish|ready|appears?))\b", _I)
_DESC_SERVICE = re.compile(r"\b(?:server|serve|serving|dev\s+server|daemon|keep\s+(?:awake|alive)|tail\s+(?:the\s+)?logs?)\b", _I)

# Options of overcode follow / jobs that take a value (the name is the first
# word that is neither an option nor one of these values)
_VALUED_OPTIONS = frozenset({
    "-n", "--lines", "-u", "--until", "-t", "--timeout", "--poll", "--on-stuck",
    "--oversight-timeout", "--session", "-s", "--interval",
})


def shell_command(argv: str) -> Optional[str]:
    """The command a Claude Bash-tool shell evals, or None for any other process."""
    if _SNAPSHOT_MARK not in argv:
        return None
    m = _EVAL.search(argv)
    if m is None:
        return None
    rest = argv[m.end():]
    try:
        lexer = shlex.shlex(rest, posix=True, punctuation_chars=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return next(iter(lexer))
    except (ValueError, StopIteration):
        return None


def _first_name(rest: str) -> Optional[str]:
    """The first positional word of an argument string (up to a ; | & or ))."""
    rest = re.split(r"[;|&)]", rest, maxsplit=1)[0]
    try:
        words = shlex.split(rest)
    except ValueError:
        words = rest.split()
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            skip = word in _VALUED_OPTIONS
            continue
        return word
    return None


def watch_target(command: str) -> Optional[Tuple[str, str]]:
    """What a watcher watches that overcode can check: ("agent" | "job", name)."""
    m = _OVERCODE_JOB.search(command)
    if m:
        name = _first_name(m.group("rest"))
        return ("job", name) if name else None
    m = _OVERCODE_FOLLOW.search(command)
    if m:
        name = _first_name(m.group("rest"))
        return ("agent", name) if name else None
    return None


def _ends_with_target(command: str) -> bool:
    """A watcher that exits when its target ends can't be stuck on it.

    ``overcode follow``, ``jobs wait`` and a streaming ``jobs tail`` all
    return when their target ends; wrapped in a retry loop, they don't.
    """
    if _POLL_LOOP.search(command):
        return False
    m = _OVERCODE_JOB.search(command)
    if m:
        verb = m.group("verb").lower()
        streaming = not re.search(r"(?:^|\s)(?:-n|--lines)\b|--no-follow\b", m.group("rest"))
        return verb == "wait" or (verb == "tail" and streaming)
    return bool(_OVERCODE_FOLLOW.search(command))


def classify(command: str, description: str = "", polls: bool = False,
             listens: bool = False) -> str:
    """service | batch | watcher, cheapest signal first.

    ``polls``: a ``sleep`` child was seen under the shell (a poll loop);
    ``listens``: something under it holds a listening TCP socket.
    """
    command = command or ""
    description = description or ""
    if (_OVERCODE_FOLLOW.search(command) or _OVERCODE_JOB.search(command)
            or _WATCH_TOOLS.search(command) or _POLL_LOOP.search(command)
            or _DELAYED_CHECK.search(command) or _TAIL_UNTIL.search(command)):
        return KIND_WATCHER
    if _SERVICES.search(command):
        return KIND_SERVICE
    if listens:
        return KIND_SERVICE
    if polls:
        return KIND_WATCHER
    if _DESC_WATCHER.search(description):
        return KIND_WATCHER
    if _DESC_SERVICE.search(description):
        return KIND_SERVICE
    return KIND_BATCH


# --- processes -----------------------------------------------------------

@dataclass(frozen=True)
class ShellProc:
    """A live Bash-tool shell: its pid, command, and every pid under it."""

    pid: int
    command: str
    pids: Tuple[int, ...] = ()
    polls: bool = False  # a `sleep` among its children right now


def shell_procs(argv_by_pid: Mapping[int, str], children: Mapping[int, List[int]],
                pids: Iterable[int]) -> List[ShellProc]:
    """The Bash-tool shells among ``pids``, with what runs under each."""
    found = []
    for pid in pids:
        command = shell_command(argv_by_pid.get(pid, ""))
        if command is None:
            continue
        under: List[int] = []
        frontier = [pid]
        while frontier and len(under) < 200:
            for child in children.get(frontier.pop(), ()):
                under.append(child)
                frontier.append(child)
        polls = any(re.match(r"(?:/bin/|/usr/bin/)?sleep\s", argv_by_pid.get(c, ""))
                    for c in children.get(pid, ()))
        found.append(ShellProc(pid, command, (pid, *under), polls))
    return found


# --- the survey ----------------------------------------------------------

# Target states a probe answers
TARGET_ALIVE = "alive"
TARGET_ENDED = "ended"
TARGET_MISSING = "missing"


@dataclass
class _Seen:
    """What one shell showed on earlier ticks."""

    pid: Optional[int] = None
    polls: bool = False
    listens: bool = False
    target_gone_at: Optional[float] = None


@dataclass
class ShellMemory:
    """Per-agent memory across ticks, by shell id."""

    shells: Dict[str, _Seen] = field(default_factory=dict)


def survey(
    listed: Mapping[str, dict],
    procs: Optional[List[ShellProc]],
    shell_ended: Callable[[str], Optional[float]],
    target_state: Callable[[str, str], Tuple[str, Optional[float]]],
    memory: ShellMemory,
    now: float,
    listening: Callable[[ShellProc], bool] = lambda p: False,
) -> List[dict]:
    """The agent's live background shells, classified, oldest first.

    ``listed``: the hook side's ``bg_shells`` (id → command, description,
    since). ``procs``: the shells the process table shows for this agent
    (None: no table this tick, so nothing is known to have exited).
    ``target_state(kind, name)`` → (alive | ended | missing, ended_at).
    Each result holds still between ticks unless something changed.
    """
    out = []
    unmatched = list(procs or [])
    keep = {}
    for shell_id, entry in sorted(listed.items(), key=lambda kv: (kv[1].get("since") or 0, kv[0])):
        if not isinstance(entry, dict) or shell_ended(shell_id) is not None:
            continue
        command = entry.get("command") or ""
        seen = memory.shells.get(shell_id) or _Seen()
        description = entry.get("description") or ""
        proc = next((p for p in unmatched if p.command == command), None)
        if proc is not None:
            unmatched.remove(proc)
            seen.pid = proc.pid
            seen.polls = seen.polls or proc.polls
            # Sockets are asked about only when the rules found nothing
            if (not seen.listens and classify(command, description, polls=seen.polls) == KIND_BATCH
                    and listening(proc)):
                seen.listens = True
        elif procs is not None and seen.pid is not None:
            continue  # it was running, and its process is gone: ended
        keep[shell_id] = seen

        kind = classify(command, description, polls=seen.polls, listens=seen.listens)
        view = {
            "id": shell_id,
            "kind": kind,
            "command": command[:COMMAND_CHARS],
            "since": entry.get("since"),
        }
        if description:
            view["description"] = description[:COMMAND_CHARS]
        if seen.pid is not None:
            view["pid"] = seen.pid
        if kind == KIND_WATCHER:
            target = watch_target(command)
            if target is not None:
                view["target"] = f"{target[0]} {target[1]}"
                state, ended_at = target_state(*target)
                if state == TARGET_ALIVE:
                    seen.target_gone_at = None
                else:
                    if seen.target_gone_at is None:
                        seen.target_gone_at = ended_at if ended_at is not None else now
                    view["target_state"] = state
                    if (not _ends_with_target(command)
                            and now - seen.target_gone_at >= STUCK_GRACE_SECONDS):
                        view["stuck_since"] = seen.target_gone_at + STUCK_GRACE_SECONDS
        out.append(view)
    memory.shells = keep
    return out


def badges(shells: List[dict]) -> List[Tuple[str, int]]:
    """(badge kind, count) for the shells, in a stable order; stuck ones too."""
    counts: Dict[str, int] = {}
    for shell in shells:
        kind = BADGE_FOR_KIND.get(shell.get("kind"), "bg_task")
        counts[kind] = counts.get(kind, 0) + 1
    order = ("bg_batch", "bg_watcher", "bg_service", "bg_task")
    return [(k, counts[k]) for k in order if k in counts]


def stuck(shells: List[dict]) -> List[dict]:
    return [s for s in shells if s.get("stuck_since") is not None]
