"""The weft claims column: the fold, finding stores, reading them, matching
claims to agents, and the cell (weft_claims.py)."""

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from overcode import weft_claims
from overcode.weft_claims import (ClaimFold, LocalProject, RemoteProject, WeftClaims,
                                  find_store, parse_ts, saved_token, shown_claim)

T0 = parse_ts("2026-10-28T12:00:00Z")


def ev(seq, verb, task, actor="ann", ts="2026-10-28T12:00:00Z", **body):
    return {"seq": seq, "ts": ts, "actor": actor, "verb": verb, "task": task, "body": body}


def fold(*events):
    f = ClaimFold()
    for e in events:
        f.apply(e)
    return f


def live(f, now):
    return {tid: (lease["actor"], lease["status"]) for tid, _, lease in f.live(now)}


# ---------------------------------------------------------------------------
# The fold, as weft's own
# ---------------------------------------------------------------------------

class TestFold:
    def test_a_claim_is_live_for_its_ttl(self):
        f = fold(ev(1, "create", "t1", title="Run the sweep"), ev(2, "claim", "t1", ttl=600))
        assert live(f, T0 + 599) == {"t1": ("ann", "working")}
        assert live(f, T0 + 600) == {}
        assert f.tasks["t1"]["title"] == "Run the sweep"

    def test_the_default_ttl_is_an_hour(self):
        f = fold(ev(1, "create", "t1", title="x"), ev(2, "claim", "t1"))
        assert live(f, T0 + 3599) and not live(f, T0 + 3600)

    def test_a_renewal_keeps_the_status_and_when_it_began(self):
        f = fold(ev(1, "create", "t1", title="x"),
                 ev(2, "claim", "t1", ttl=600, status="stuck"),
                 ev(3, "claim", "t1", ts="2026-10-28T12:09:00Z", ttl=600))
        lease = f.tasks["t1"]["lease"]
        assert (lease["status"], lease["since"]) == ("stuck", "2026-10-28T12:00:00Z")
        assert lease["until"] == T0 + 540 + 600

    def test_a_new_status_restarts_since(self):
        f = fold(ev(1, "create", "t1", title="x"), ev(2, "claim", "t1"),
                 ev(3, "claim", "t1", ts="2026-10-28T12:05:00Z", status="waiting"))
        assert f.tasks["t1"]["lease"]["since"] == "2026-10-28T12:05:00Z"

    def test_another_actor_after_a_lapse_starts_working(self):
        f = fold(ev(1, "create", "t1", title="x"), ev(2, "claim", "t1", ttl=60, status="stuck"),
                 ev(3, "claim", "t1", actor="bob", ts="2026-10-28T13:00:00Z"))
        assert live(f, T0 + 3601) == {"t1": ("bob", "working")}

    @pytest.mark.parametrize("verb", ["release", "close"])
    def test_release_and_close_end_the_lease(self, verb):
        f = fold(ev(1, "create", "t1", title="x"), ev(2, "claim", "t1"), ev(3, verb, "t1", reason="produced"))
        assert live(f, T0 + 1) == {}

    def test_seen_events_are_skipped(self):
        f = fold(ev(1, "create", "t1", title="x"), ev(2, "claim", "t1"), ev(3, "release", "t1"))
        f.apply(ev(2, "claim", "t1"))
        assert live(f, T0 + 1) == {} and f.seq == 3

    def test_events_with_no_task_or_a_bad_body_are_ignored(self):
        f = fold(ev(1, "comment", None, text="hi"), ev(2, "define", None, name="x", type="number"),
                 {"seq": 3, "ts": "garbage", "actor": "a", "verb": "claim", "task": "t9", "body": {}},
                 {"seq": 4, "verb": "claim", "task": "t8", "actor": "a", "ts": "2026-10-28T12:00:00Z",
                  "body": {"ttl": "long"}})
        assert live(f, T0 + 1) == {"t8": ("a", "working")}


# ---------------------------------------------------------------------------
# Finding a store, as the weft CLI does
# ---------------------------------------------------------------------------

class TestFindStore:
    def test_the_nearest_weft_above(self, tmp_path):
        (tmp_path / ".weft").mkdir()
        (tmp_path / ".weft" / "weft.db").write_text("")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert find_store(str(deep), repo_root=lambda d: None) == str(tmp_path / ".weft" / "weft.db")

    def test_a_server_url(self, tmp_path):
        (tmp_path / ".weft").mkdir()
        (tmp_path / ".weft" / "url").write_text("https://box:7411/proj\n")
        assert find_store(str(tmp_path), repo_root=lambda d: None) == "https://box:7411/proj"

    def test_a_db_beats_a_url_in_the_same_place(self, tmp_path):
        (tmp_path / ".weft").mkdir()
        (tmp_path / ".weft" / "url").write_text("https://box:7411/proj")
        (tmp_path / ".weft" / "weft.db").write_text("")
        assert find_store(str(tmp_path), repo_root=lambda d: None).endswith("weft.db")

    def test_a_worktree_finds_the_main_checkout_s(self, tmp_path):
        main, tree = tmp_path / "main", tmp_path / "trees" / "x"
        (main / ".weft").mkdir(parents=True)
        (main / ".weft" / "url").write_text("https://box:7411/proj")
        tree.mkdir(parents=True)
        assert find_store(str(tree), repo_root=lambda d: main) == "https://box:7411/proj"

    def test_none(self, tmp_path):
        assert find_store(str(tmp_path), repo_root=lambda d: None) is None


def test_the_token_is_the_longest_matching_prefix(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens"
    tokens.write_text("# comment\nhttps://box:7411/ short\nhttps://box:7411/proj long\nhttps://other/ x\n")
    monkeypatch.setenv("WEFT_TOKENS", str(tokens))
    monkeypatch.delenv("WEFT_TOKEN", raising=False)
    assert saved_token("https://box:7411/proj") == "long"
    assert saved_token("https://box:7411/else") == "short"
    assert saved_token("https://nowhere/p") is None
    monkeypatch.setenv("WEFT_TOKEN", "env")
    assert saved_token("https://box:7411/proj") == "env"


# ---------------------------------------------------------------------------
# Reading stores
# ---------------------------------------------------------------------------

def make_db(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists events (seq integer primary key autoincrement, "
                 "ts text not null, actor text not null, verb text not null, task text, body text not null)")
    for e in events:
        conn.execute("insert into events (ts, actor, verb, task, body) values (?, ?, ?, ?, ?)",
                     (e["ts"], e["actor"], e["verb"], e["task"], json.dumps(e["body"])))
    conn.commit()
    conn.close()


class TestLocalProject:
    def test_reads_from_where_it_stopped(self, tmp_path):
        db = tmp_path / "proj" / ".weft" / "weft.db"
        make_db(db, [ev(0, "create", "t1", title="x"), ev(0, "claim", "t1")])
        project = LocalProject(str(db))
        project.poll()
        assert project.name == "proj" and project.fold.seq == 2
        assert live(project.fold, T0 + 1) == {"t1": ("ann", "working")}
        make_db(db, [ev(0, "release", "t1")])
        project.poll()
        assert project.fold.seq == 3 and live(project.fold, T0 + 1) == {}
        project.close()

    def test_an_unreadable_store_is_logged_not_raised(self, tmp_path):
        logged = []
        project = LocalProject(str(tmp_path / "missing" / ".weft" / "weft.db"), log=logged.append)
        project.poll()
        assert logged and project.fold.seq == 0


class TestRemoteProject:
    def test_follows_and_reconnects_from_the_last_event(self):
        calls, done = [], threading.Event()

        def follow(url, since, token):
            calls.append(since)
            if len(calls) == 1:
                yield ev(1, "create", "t1", title="x")
                yield ev(2, "claim", "t1")
                raise OSError("dropped")
            yield ev(3, "claim", "t1", status="stuck")
            done.set()

        slept, logged = [], []
        project = RemoteProject("https://box:7411/proj", log=logged.append, follow=follow,
                                sleep=slept.append)
        project.poll()
        assert done.wait(5)
        project.close()
        project._thread.join(5)
        assert calls[:2] == [0, 2] and slept[0] == weft_claims.RETRY_FIRST
        assert len(logged) == 1 and "dropped" in logged[0]
        assert {tid: lease["status"] for tid, _, lease in project.live(T0 + 1)} == {"t1": "stuck"}
        assert project.name == "proj"


# ---------------------------------------------------------------------------
# What each agent holds
# ---------------------------------------------------------------------------

def agent(sid, name, directory, previous=()):
    return SimpleNamespace(id=sid, name=name, start_directory=str(directory),
                           previous_names=list(previous))


class TestWeftClaims:
    def _setup(self, tmp_path, events):
        db = tmp_path / "proj" / ".weft" / "weft.db"
        make_db(db, events)
        return tmp_path / "proj"

    def test_claims_go_to_the_agent_named_by_their_actor(self, tmp_path):
        proj = self._setup(tmp_path, [
            ev(0, "create", "t1", title="Run the sweep"), ev(0, "claim", "t1", actor="alpha", ttl=600),
            ev(0, "create", "t2", title="Write it up"),
            ev(0, "claim", "t2", actor="alpha", ts="2026-10-28T12:01:00Z", status="stuck"),
            ev(0, "create", "t3", title="Someone else's"), ev(0, "claim", "t3", actor="mike")])
        claims = WeftClaims(find=lambda d: find_store(d, repo_root=lambda _: None), clock=lambda: T0 + 1)
        claims.refresh([agent("a", "alpha", proj), agent("b", "bravo", proj)])
        held = claims.of("a")
        assert [(c["task"], c["title"], c["status"], c["project"]) for c in held] == [
            ("t1", "Run the sweep", "working", "proj"), ("t2", "Write it up", "stuck", "proj")]
        assert held[0]["until"] == "2026-10-28T12:10:00Z"
        assert claims.of("b") is None
        claims.refresh([agent("a", "alpha", proj)], now=T0 + 601)
        assert [c["task"] for c in claims.of("a")] == ["t2"]
        claims.close()

    def test_a_renamed_agent_keeps_its_claims_unless_its_old_name_is_taken(self, tmp_path):
        proj = self._setup(tmp_path, [ev(0, "create", "t1", title="x"),
                                      ev(0, "claim", "t1", actor="agent-3")])
        claims = WeftClaims(find=lambda d: find_store(d, repo_root=lambda _: None), clock=lambda: T0 + 1)
        claims.refresh([agent("a", "fix-jwt", proj, previous=["agent-3"])])
        assert [c["task"] for c in claims.of("a")] == ["t1"]
        claims.refresh([agent("a", "fix-jwt", proj, previous=["agent-3"]), agent("b", "agent-3", proj)])
        assert claims.of("a") is None and [c["task"] for c in claims.of("b")] == ["t1"]
        claims.close()

    def test_a_directory_is_looked_at_again_after_a_while(self, tmp_path):
        now = [T0]
        looked = []

        def find(d):
            looked.append(d)
            return None

        claims = WeftClaims(find=find, clock=lambda: now[0])
        sessions = [agent("a", "alpha", tmp_path), agent("b", "bravo", tmp_path)]
        claims.refresh(sessions)
        claims.refresh(sessions)
        now[0] += weft_claims.DISCOVERY_SECONDS
        claims.refresh(sessions)
        assert looked == [str(tmp_path), str(tmp_path)]

    def test_a_server_store_is_followed_once(self, tmp_path):
        made = []

        class Remote:
            def __init__(self, url, log):
                self.name, self.polls = url.rsplit("/", 1)[-1], 0
                made.append(self)

            def poll(self):
                self.polls += 1

            def live(self, now):
                return [("t1", {"title": "x"}, {"actor": "alpha", "status": "waiting",
                                                "since": "2026-10-28T12:00:00Z", "until": T0 + 60})]

            def close(self):
                pass

        claims = WeftClaims(find=lambda d: "https://box:7411/proj", clock=lambda: T0,
                            remote=Remote)
        claims.refresh([agent("a", "alpha", tmp_path)])
        claims.refresh([agent("a", "alpha", tmp_path)])
        assert len(made) == 1 and made[0].polls == 2
        assert [(c["task"], c["status"], c["project"]) for c in claims.of("a")] == [
            ("t1", "waiting", "proj")]


def test_the_daemon_publishes_an_agent_s_claims(tmp_path, monkeypatch):
    """Whole ticks: a claim made in the agent's directory shows on the next
    tick, the other agent shows none, and a release clears it."""
    from datetime import datetime

    from overcode.session_manager import SessionManager
    from overcode.weft_claims import stamp
    from tests.daemon_tick_harness import (FrozenClock, ScriptedDetector, make_daemon,
                                           run_ticks, seed_sessions, seed_steady_state,
                                           set_sync_stamps)

    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    start = datetime(2026, 10, 28, 12, 0, 0)
    sm = SessionManager(state_dir=home / ".overcode" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, 2, "agents", tmp_path / "work", start)
    db = tmp_path / "work" / "repo-00" / ".weft" / "weft.db"
    make_db(db, [ev(0, "create", "t1", title="Run the sweep")])
    detector = ScriptedDetector(lambda tick, s: ("running", "Working", "pane"))
    daemon = make_daemon(home / ".overcode", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, start)
    published = []

    def between(tick):
        set_sync_stamps(daemon, clock.now)
        by_name = {s.name: s.weft_claims for s in daemon.state.sessions}
        published.append(([c["task"] for c in by_name["agent-00"] or ()], by_name["agent-01"]))
        now = stamp(clock.time())
        if tick == 1:
            make_db(db, [ev(0, "claim", "t1", actor="agent-00", ts=now)])
        if tick == 3:
            make_db(db, [ev(0, "release", "t1", actor="agent-00", ts=now)])

    with FrozenClock(start).installed() as clock:
        run_ticks(daemon, detector, clock, 5, between_ticks=between)
    daemon.weft_claims.close()
    assert published == [([], None), ([], None), (["t1"], None), (["t1"], None), ([], None)]


def test_the_state_round_trips():
    from overcode.monitor_daemon_state import SessionDaemonState
    claims = [{"task": "t1", "title": "x", "status": "working", "since": "2026-10-28T12:00:00Z",
               "until": "2026-10-28T13:00:00Z", "actor": "a", "project": "p"}]
    state = SessionDaemonState(session_id="s", name="n", weft_claims=claims)
    assert SessionDaemonState.from_dict(state.to_dict()).weft_claims == claims
    assert SessionDaemonState.from_dict({"session_id": "s"}).weft_claims is None


# ---------------------------------------------------------------------------
# The cell
# ---------------------------------------------------------------------------

def claim(task, status="working", since="2026-10-28T12:00:00Z", title="Run the parameter sweep"):
    return {"task": task, "title": title, "status": status, "since": since,
            "until": "2026-10-28T13:00:00Z", "actor": "a", "project": "proj"}


def _text(**kw):
    from overcode.columns.render_supervision import render_weft
    from tests.unit.test_summary_columns import _make_ctx
    return render_weft(_make_ctx(**kw))[0]


class TestCell:
    def test_no_claim_is_the_same_shape(self):
        text, style = _text(weft_claims=None)
        assert text == " 🧶  -" and style.startswith("dim")

    def test_the_title_is_cut_by_detail_level(self):
        assert _text(weft_claims=[claim("t-1")], summary_detail="low")[0] == " 🧶 t-1"
        assert _text(weft_claims=[claim("t-1")], summary_detail="med")[0] == " 🧶 t-1 Run the par…"
        assert _text(weft_claims=[claim("t-1")], summary_detail="full")[0] == " 🧶 t-1 Run the parameter swe…"

    def test_a_stuck_claim_shows_first_with_the_rest_counted(self):
        claims = [claim("t-1", "stuck"), claim("t-2", since="2026-10-28T12:30:00Z")]
        text, style = _text(weft_claims=claims, summary_detail="low")
        assert text == " 🧶 t-1 · stuck +1" and "red" in style

    def test_emoji_free(self):
        assert _text(weft_claims=[claim("t-1", "waiting")], summary_detail="low",
                     emoji_free=True)[0] == " Wv t-1 · waiting"

    def test_the_newest_when_none_is_stuck(self):
        assert shown_claim([claim("a"), claim("b", since="2026-10-28T12:30:00Z")])["task"] == "b"
        assert shown_claim([]) is None
