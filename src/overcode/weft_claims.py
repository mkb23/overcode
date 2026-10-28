"""Which weft tasks each agent holds a live claim on (the 🧶 column).

weft (https://github.com/mkb23/weft) is a shared work graph: an
append-only log of events about tasks, kept in one SQLite table per
project, either on this box (``.weft/weft.db``) or behind ``weft serve``
(``.weft/url``). An agent ``weft claim``s a task for a lease that lapses
unless renewed, with a status: working, waiting or stuck.

overcode only watches. It never claims, renews or records for an agent:
how an agent uses weft is the agent's call. What it shows is what the log
says each agent holds right now.

* **Projects** are found the way the weft CLI finds its store, from each
  agent's start directory: the nearest ``.weft/`` above it holding
  ``weft.db`` (then ``url``), else the one at the repo's main checkout.
* **Events** are read from where the last read stopped: a local store on
  every tick (one indexed query), a server's over one ``watch`` stream per
  project, followed on a thread so the network never holds up a tick.
* **Agents** are matched by actor: the agent's name, or a name it had
  before a rename. overcode launches agents with ``WEFT_ACTOR`` set to
  their name; through a server the actor comes from the token instead.

Only claims are folded (claim, release, close, reopen, and titles). The
store has already enforced weft's rules, so the fold trusts the log. A
lease is live while ``until`` is ahead of the daemon's clock: one that
lapses drops out on the next tick, with no event.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional

DEFAULT_TTL = 3600  # weft's default lease, in seconds
# How long a directory's project lookup is trusted: a `weft init` shows up
# within this, without walking every agent's directory on every tick
DISCOVERY_SECONDS = 60.0
# A server stream's reconnect backoff, doubling from the first to the last
RETRY_FIRST, RETRY_LAST = 5.0, 300.0
# The server pings every 15 s while idle, so a silent minute is a dead stream
STREAM_TIMEOUT = 60.0
TITLE_CHARS = 120


def parse_ts(ts: str) -> Optional[float]:
    """A weft timestamp ("2026-10-27T21:40:00Z") as epoch seconds."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------

class ClaimFold:
    """Tasks' titles and leases, folded from a project's events in order."""

    def __init__(self) -> None:
        self.seq = 0
        self.tasks: Dict[str, dict] = {}

    def apply(self, e: dict) -> None:
        if not isinstance(e, dict):
            return
        seq = e.get("seq")
        if isinstance(seq, int):
            if seq <= self.seq:  # a stream replays nothing twice
                return
            self.seq = seq
        task, verb = e.get("task"), e.get("verb")
        body = e.get("body") if isinstance(e.get("body"), dict) else {}
        if not isinstance(task, str) or not task:
            return
        t = self.tasks.setdefault(task, {"title": None, "lease": None})
        if verb in ("create", "update") and isinstance(body.get("title"), str):
            t["title"] = body["title"][:TITLE_CHARS]
        elif verb == "claim":  # a claim is also a heartbeat: its status persists until changed
            start = parse_ts(e.get("ts"))
            if start is None:
                return
            ttl = body.get("ttl", DEFAULT_TTL)
            ttl = ttl if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) else DEFAULT_TTL
            old = t["lease"] if t["lease"] and t["lease"]["actor"] == e.get("actor") else {}
            status = body.get("status") or old.get("status", "working")
            t["lease"] = {"actor": e.get("actor"), "until": start + ttl, "status": status,
                          "since": old["since"] if old.get("status") == status else e.get("ts")}
        elif verb in ("release", "close"):
            t["lease"] = None

    def live(self, now: float) -> Iterator[tuple]:
        """(task id, task, lease) for every lease still running at ``now``."""
        for tid, t in self.tasks.items():
            lease = t["lease"]
            if lease and lease["until"] > now:
                yield tid, t, lease


# ---------------------------------------------------------------------------
# Projects: a store on this box, or a server's
# ---------------------------------------------------------------------------

class LocalProject:
    """A ``.weft/weft.db`` on this box, read on each tick from where it stopped."""

    def __init__(self, path: str, log: Callable[[str], None] = lambda m: None) -> None:
        self.target = path
        self.name = Path(path).parent.parent.name
        self.fold = ClaimFold()
        self._log = log
        self._conn: Optional[sqlite3.Connection] = None

    def poll(self) -> None:
        try:
            if self._conn is None:
                self._conn = sqlite3.connect(f"file:{self.target}?mode=ro", uri=True, timeout=1.0)
            rows = self._conn.execute(
                "select seq, ts, actor, verb, task, body from events where seq > ? order by seq",
                (self.fold.seq,)).fetchall()
        except sqlite3.Error as e:
            self._log(f"weft store {self.target} not read: {e}")
            self.close()
            return
        for seq, ts, actor, verb, task, body in rows:
            try:
                body = json.loads(body)
            except (TypeError, ValueError):
                body = {}
            self.fold.apply({"seq": seq, "ts": ts, "actor": actor, "verb": verb,
                             "task": task, "body": body})

    def live(self, now: float) -> list:
        return list(self.fold.live(now))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def saved_token(url: str) -> Optional[str]:
    """The token for ``url``, as the weft CLI finds it: ``$WEFT_TOKEN``, else
    ``~/.config/weft/tokens`` (``$WEFT_TOKENS``), lines of "<url-prefix>
    <token>", the longest matching prefix winning."""
    if os.environ.get("WEFT_TOKEN"):
        return os.environ["WEFT_TOKEN"]
    path = Path(os.environ.get("WEFT_TOKENS") or Path.home() / ".config" / "weft" / "tokens")
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    matches = [line.split()[:2] for line in lines
               if len(line.split()) >= 2 and not line.startswith("#") and url.startswith(line.split()[0])]
    return max(matches, key=lambda m: len(m[0]))[1] if matches else None


def follow_url(url: str, since: int, token: Optional[str]) -> Iterator[dict]:
    """A server's events after ``since``, then live (``GET /events?since=N&follow=1``)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(f"{url.rstrip('/')}/events?since={since}&follow=1", headers=headers)
    with urllib.request.urlopen(request, timeout=STREAM_TIMEOUT) as stream:
        for line in stream:
            if line.startswith(b"data: "):
                yield json.loads(line[6:])


class RemoteProject:
    """A project behind ``weft serve``, followed on a thread of its own."""

    def __init__(self, url: str, log: Callable[[str], None] = lambda m: None,
                 follow: Callable[[str, int, Optional[str]], Iterable[dict]] = follow_url,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.target = url
        self.name = url.rstrip("/").rsplit("/", 1)[-1]
        self.fold = ClaimFold()
        self._log, self._follow, self._sleep = log, follow, sleep
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def poll(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"weft:{self.name}", daemon=True)
            self._thread.start()

    def live(self, now: float) -> list:
        with self._lock:
            return list(self.fold.live(now))

    def _run(self) -> None:
        wait, failing = RETRY_FIRST, False
        while not self._stop.is_set():
            try:
                for event in self._follow(self.target, self.fold.seq, saved_token(self.target)):
                    with self._lock:
                        self.fold.apply(event)
                    wait, failing = RETRY_FIRST, False
                    if self._stop.is_set():
                        return
            except Exception as e:  # a stream drops; it is followed again
                if not failing:
                    self._log(f"weft {self.target} not followed: {type(e).__name__}: {e}")
                failing = True
            if self._stop.is_set():
                return
            self._sleep(wait)
            wait = min(wait * 2, RETRY_LAST)

    def close(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Finding projects, and what each agent holds
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=512)
def _repo_root(directory: str) -> Optional[Path]:
    """The main checkout of ``directory``'s repo, so worktrees share its store.
    Asked once per directory: a repo's main checkout does not move."""
    try:
        common = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(common).parent if common.endswith(".git") else None


def find_store(directory: str, repo_root: Callable[[str], Optional[Path]] = _repo_root) -> Optional[str]:
    """The store the weft CLI would use in ``directory`` (without ``$WEFT``):
    a db path or a server URL, or None."""
    here = Path(directory)
    for d in [here, *here.parents]:
        store = _store_in(d)
        if store:
            return store
    root = repo_root(directory)  # only when nothing above it has one
    return _store_in(root) if root else None


def _store_in(d: Path) -> Optional[str]:
    try:
        if (d / ".weft" / "weft.db").exists():
            return str(d / ".weft" / "weft.db")
        if (d / ".weft" / "url").exists():
            return (d / ".weft" / "url").read_text().strip() or None
    except OSError:
        pass
    return None


class WeftClaims:
    """Every agent's live weft claims, refreshed once per daemon tick."""

    def __init__(self, log: Callable[[str], None] = lambda m: None,
                 find: Callable[[str], Optional[str]] = find_store,
                 clock: Callable[[], float] = time.time,
                 remote: Callable[..., RemoteProject] = RemoteProject) -> None:
        self._log, self._find, self._clock, self._remote = log, find, clock, remote
        self._stores: Dict[str, tuple] = {}  # directory -> (looked at, store or None)
        self.projects: Dict[str, object] = {}  # store -> LocalProject | RemoteProject
        self._by_session: Dict[str, list] = {}

    def _store_of(self, directory: str) -> Optional[str]:
        now = self._clock()
        seen = self._stores.get(directory)
        if seen is None or now - seen[0] >= DISCOVERY_SECONDS:
            seen = self._stores[directory] = (now, self._find(directory))
        return seen[1]

    def _project(self, store: str):
        project = self.projects.get(store)
        if project is None:
            project = (self._remote(store, log=self._log) if store.startswith(("http://", "https://"))
                       else LocalProject(store, log=self._log))
            self.projects[store] = project
        return project

    def refresh(self, sessions: list, now: Optional[float] = None) -> None:
        """Read each project the fleet's directories lead to, then give every
        live claim to the agent its actor names: by current name first, so
        a name another agent once had goes to the one that has it now."""
        now = self._clock() if now is None else now
        stores = {self._store_of(d) for d in {getattr(s, "start_directory", None) for s in sessions} if d}
        stores.discard(None)
        for store in stores:
            self._project(store).poll()
        owner: Dict[str, str] = {}
        for s in sessions:
            owner.setdefault(s.name, s.id)
        for s in sessions:
            for name in getattr(s, "previous_names", None) or ():
                owner.setdefault(name, s.id)
        by_session: Dict[str, list] = {}
        for store in sorted(stores):
            project = self.projects[store]
            for tid, task, lease in project.live(now):
                sid = owner.get(lease["actor"])
                if sid is not None:
                    by_session.setdefault(sid, []).append({
                        "task": tid, "title": task["title"], "status": lease["status"],
                        "since": lease["since"], "until": stamp(lease["until"]),
                        "actor": lease["actor"], "project": project.name})
        for claims in by_session.values():
            claims.sort(key=lambda c: (c["since"] or "", c["task"]))
        self._by_session = by_session

    def of(self, session_id: str) -> Optional[list]:
        """The agent's live claims, oldest first, or None if it holds none."""
        return self._by_session.get(session_id)

    def close(self) -> None:
        for project in self.projects.values():
            project.close()


# ---------------------------------------------------------------------------
# For the column
# ---------------------------------------------------------------------------

def shown_claim(claims: Optional[list]) -> Optional[dict]:
    """The claim a row shows: a stuck one first (it wants you), else the newest."""
    claims = [c for c in claims or () if isinstance(c, dict)]
    if not claims:
        return None
    return max(claims, key=lambda c: (c.get("status") == "stuck", c.get("since") or ""))


def describe_claim(claim: dict) -> str:
    """One claim on one line: "t-3c9e01 Run the sweep · working · until 21:40 (useful-bit)"."""
    parts = [f"{claim.get('task')} {claim.get('title') or ''}".rstrip(),
             claim.get("status") or "working"]
    until = parse_ts(claim.get("until"))
    if until is not None:
        parts.append(f"until {datetime.fromtimestamp(until).strftime('%H:%M')}")
    text = " · ".join(parts)
    return f"{text} ({claim['project']})" if claim.get("project") else text
