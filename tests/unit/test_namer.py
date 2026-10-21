"""The auto-namer (#542, auto-naming §3-5, §7-8), replayed on synthetic fleets.

A stub model stands in for the summarizer; the namer's worker runs inline
(``submit``). Turn ends are fed as daemon tick states (green -> waiting),
so the trigger cadence, the human/parent lock, the choice among
candidates, the cost cap, the ledger row and the billing code are checked
as the daemon would drive them. The Claude title is checked against a
MockTmux pane and synthetic transcript records.
"""

import io
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from overcode import namer as nm
from overcode.launcher import AgentLauncher
from overcode.paths import agent_dir, agent_file
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

T0 = datetime(2026, 10, 20, 12, 0, 0)


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    for var in list(os.environ):
        if var.startswith("OVERCODE_"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "home"))
    monkeypatch.setattr("overcode.launcher.require_tmux", lambda: "tmux")
    monkeypatch.setattr("overcode.launcher.require_agent_cli", lambda backend: "claude")
    yield tmp_path


def _cfg(**over):
    cfg = {"enabled": True, "advice": "", "max_length": 18, "rules": {},
           "first_after": {"minutes": 4},   # two Fleet.turn()s after the launch prompt
           "max_renames": 2, "cost_cap_per_day": 0.50, "provider": None, "cli_title": True}
    cfg.update(over)
    return cfg


class StubModel:
    """The summarizer client, answering from a list of replies."""

    def __init__(self, *replies, tokens=(1000, 40), model="gpt-4o-mini"):
        self.replies = list(replies)
        self.prompts = []
        self.available = True
        self.model = model
        self.last_input_tokens, self.last_output_tokens = tokens

    def complete(self, prompt, max_tokens=150, timeout=15.0):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else None
        return json.dumps(reply) if isinstance(reply, dict) else reply


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def wall(self):
        return self.now.astimezone()

    def time(self):
        return self.now.timestamp()

    def monotonic(self):
        return self.now.timestamp()


class Fleet:
    """Agents in a temp registry, and a namer over them driven tick by tick."""

    def __init__(self, tmp_path, *, cfg=None, model=None, keypress_age=None, tmux=None):
        self.sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        self.clock = Clock()
        self.cfg = cfg or _cfg()
        self.model = model or StubModel()
        self.log = []
        self.renames = []
        self.tmux = tmux
        self.titles = {}
        self.namer = nm.Namer(
            "agents", self.sm, tmux=tmux, clock=self.clock,
            log=SimpleNamespace(info=self.log.append, warn=self.log.append),
            titles=lambda: self.titles,
            rename=self._rename, client_factory=lambda provider: self.model,
            submit=lambda fn: fn(), config=lambda: self.cfg,
            keypress_age=keypress_age or (lambda: None),
            spend_path=tmp_path / "namer_spend.json",
        )
        self.green = {}

    def _rename(self, session, new_name, source):
        self.renames.append((session.name, new_name, source))
        if not self.sm.rename_session(session.id, new_name, name_source=source):
            raise ValueError(f"an agent named '{new_name}' already exists")
        return True

    def add(self, name, *, source="default", parent=None, repo="overcode", backend="claude-code",
            brief="Fix the JWT refresh race in auth/session.py"):
        s = self.sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-w",
                                   command=["claude"], name_source=source, backend=backend)
        # Launched now, by the test's clock: with a brief, its task time (first_task_at)
        fields = {"repo_name": repo, "branch": "main", "start_time": self.clock.wall().isoformat()}
        if parent is not None:
            fields["parent_session_id"] = parent.id
        self.sm.update_session(s.id, **fields)
        agent_dir("agents", s.id).mkdir(parents=True, exist_ok=True)
        if brief:
            agent_file("agents", s.id, "brief").write_text(brief)
        self.green[s.id] = 0.0
        return self.sm.get_session(s.id)

    def get(self, session):
        return self.sm.get_session(session.id)

    def turn(self, *agents, minutes=2.0, tokens=0, end="waiting_user"):
        """Each agent works ``minutes`` (green), then stops: two ticks."""
        self.clock.now += timedelta(minutes=minutes)
        for a in agents:
            self.green[a.id] += minutes * 60
        for status in ("running", end):
            states = [SimpleNamespace(session_id=a.id, current_status=status,
                                      green_time_seconds=self.green[a.id], output_tokens=tokens)
                      for a in agents]
            # Read each tick, as the daemon does: a name given mid-turn shows at the Stop
            self.namer.on_tick(self.sm.list_sessions(), states, self.clock.wall())

    def idle(self, *agents, seconds=0.0, status="waiting_user"):
        """One tick with each agent sitting idle, ``seconds`` after the last."""
        self.clock.now += timedelta(seconds=seconds)
        states = [SimpleNamespace(session_id=a.id, current_status=status,
                                  green_time_seconds=self.green[a.id], output_tokens=0)
                  for a in agents]
        self.namer.on_tick(self.sm.list_sessions(), states, self.clock.wall())

    def state(self, session):
        return nm.load_state("agents", session.id)


def _reply(*names, keep=False, why="the task"):
    return {"candidates": list(names), "keep": keep, "why": why}


# ── when to name (§3) ────────────────────────────────────────────────


class TestDue:

    def _first(self, state=None, **kw):
        args = dict(source="default", task_since=T0.timestamp() - 300, green_seconds=5,
                    cfg=_cfg(first_after={"minutes": 5}), now=T0.timestamp())
        args.update(kw)
        return nm.first_due(state or nm.NamerState(), **args)

    def _due(self, state=None, **kw):
        args = dict(source="auto", turns=20, green_seconds=1800, output_tokens=0, cfg=_cfg(),
                    now=T0.timestamp())
        args.update(kw)
        return nm.due(state or nm.NamerState(first_done=True, renames=1,
                                                last_rename_at=T0.timestamp() - 3600), **args)

    def test_first_name_minutes_after_the_first_prompt_once_it_has_worked(self):
        assert self._first()
        assert not self._first(task_since=T0.timestamp() - 299)
        assert not self._first(task_since=None)         # never given a task
        assert not self._first(green_seconds=0)         # given one, never started on it
        assert self._first(cfg=_cfg(first_after={"minutes": 0}), task_since=T0.timestamp())

    @pytest.mark.parametrize("source", ["human", "parent", None, "made-up"])
    def test_locked_names_are_never_due(self, source):
        assert not self._first(source=source)
        assert self._due(source=source) is None

    def test_named_once_and_retried_ten_minutes_after_a_call(self):
        assert not self._first(nm.NamerState(first_done=True))
        assert not self._first(nm.NamerState(renames=1))
        assert not self._first(nm.NamerState(attempted_at=T0.timestamp() - 599))
        assert self._first(nm.NamerState(attempted_at=T0.timestamp() - 601))

    def test_refinement_at_the_fixed_bar(self):
        assert self._due(turns=19) is None
        assert self._due(green_seconds=1799) is None
        assert self._due() == "refine"
        assert self._due(green_seconds=0, output_tokens=150000) == "refine"
        assert self._due(nm.NamerState()) is None              # no first name yet
        assert self._due(nm.NamerState(first_done=True, refine_done=True)) is None

    def test_refinement_within_limits(self):
        st = nm.NamerState(first_done=True, renames=1, last_rename_at=T0.timestamp() - 599)
        assert self._due(st) is None
        st = nm.NamerState(first_done=True, renames=2)
        assert self._due(st) is None
        assert self._due(cfg=_cfg(max_renames=1)) is None


# ── the namer, replayed ──────────────────────────────────────────────


class TestReplay:

    def test_first_name_at_the_second_stop_then_one_refinement(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-refresh", "auth-refresh"),
                                                _reply("jwt-refresh", keep=True, why="same")))
        a = fleet.add("overcode-main2")

        fleet.turn(a)                              # 1 turn, 2 min
        assert fleet.model.prompts == []
        fleet.turn(a)                              # 2 turns, 4 min: due
        assert len(fleet.model.prompts) == 1
        renamed = fleet.get(a)
        assert renamed.name == "jwt-refresh"
        assert renamed.name_source == "auto"
        assert renamed.previous_names == ["overcode-main2"]
        assert fleet.state(a).renames == 1
        assert any("auto-named 'overcode-main2' → 'jwt-refresh'" in m for m in fleet.log)

        for _ in range(17):                        # up to 19 turns, 38 min
            fleet.turn(a)
        assert len(fleet.model.prompts) == 1
        fleet.turn(a)                              # 20 turns, 40 min: refinement
        assert len(fleet.model.prompts) == 2
        assert fleet.get(a).name == "jwt-refresh"  # keep: true
        for _ in range(30):
            fleet.turn(a, minutes=10)
        assert len(fleet.model.prompts) == 2       # nothing after the refinement
        assert fleet.state(a).refine_done

    def test_a_refinement_renames_when_the_task_changed(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-refresh"), _reply("db-migrate")))
        a = fleet.add("overcode-main2")
        for _ in range(20):
            fleet.turn(a)
        got = fleet.get(a)
        assert got.name == "db-migrate" and got.previous_names == ["overcode-main2", "jwt-refresh"]
        assert fleet.state(a).renames == 2

    def test_a_quick_task_is_named_while_it_waits(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("big-refactor")))
        a = fleet.add("tmp1")
        fleet.turn(a, minutes=0.3)                 # 18 s of work, then idle
        assert fleet.model.prompts == []
        fleet.idle(a, seconds=3 * 60)              # 3.3 min after its prompt
        assert fleet.model.prompts == []
        fleet.idle(a, seconds=60)                  # 4.3 min: due, though idle all along
        assert fleet.get(a).name == "big-refactor"

    def test_a_launch_without_a_prompt_waits_for_one(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("db-migrate")))
        a = fleet.add("tmp2", brief="")            # no launch prompt
        for _ in range(5):
            fleet.idle(a, seconds=600)
        fleet.turn(a, minutes=0.5)                 # works, but no prompt on record yet
        assert fleet.model.prompts == []
        typed_at = fleet.clock.now.timestamp()
        agent_file("agents", a.id, "prompts").write_text(
            json.dumps({"t": typed_at, "prompt": "migrate the db"}) + "\n")
        fleet.idle(a, seconds=61)
        assert fleet.model.prompts == []           # 1 min after it
        fleet.idle(a, seconds=4 * 60)
        assert fleet.get(a).name == "db-migrate"

    @pytest.mark.parametrize("source", ["human", "parent"])
    def test_human_and_parent_names_are_never_renamed(self, tmp_path, source):
        fleet = Fleet(tmp_path, model=StubModel(_reply("better")))
        a = fleet.add("my-name", source=source)
        for _ in range(30):
            fleet.turn(a, minutes=10)
        assert fleet.model.prompts == [] and fleet.get(a).name == "my-name"

    def test_a_name_locked_while_the_call_runs_is_kept(self, tmp_path):
        fleet = Fleet(tmp_path)
        a = fleet.add("work")

        def reply_and_lock(prompt, **kw):
            fleet.sm.update_session(a.id, name_source="human")  # Ctrl+N meanwhile
            return json.dumps(_reply("other"))
        fleet.model.complete = reply_and_lock
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "work" and fleet.renames == []

    def test_waiting_approval_is_not_a_stop(self, tmp_path):
        fleet = Fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        for _ in range(4):
            fleet.turn(a, end="waiting_approval")
        assert fleet.state(a).turns == 0

    def test_not_while_a_person_is_typing_in_the_tui(self, tmp_path):
        age = {"value": 5.0}
        fleet = Fleet(tmp_path, model=StubModel(_reply("x-name")), keypress_age=lambda: age["value"])
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.model.prompts == []
        age["value"] = 31.0
        fleet.turn(a)
        assert fleet.get(a).name == "x-name"

    def test_off_by_default_counts_turns_only(self, tmp_path):
        fleet = Fleet(tmp_path, cfg=_cfg(enabled=False), model=StubModel(_reply("x-name")))
        a = fleet.add("work")
        for _ in range(5):
            fleet.turn(a)
        assert fleet.model.prompts == [] and fleet.state(a).turns == 5

    def test_a_failed_call_is_retried_ten_minutes_later(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel("not json at all", _reply("x-name")))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert len(fleet.model.prompts) == 1 and fleet.get(a).name == "work"
        assert any("unreadable reply" in m for m in fleet.log)
        fleet.turn(a, minutes=5)
        assert len(fleet.model.prompts) == 1
        fleet.turn(a, minutes=6)
        assert fleet.get(a).name == "x-name"

    def test_no_model_configured_is_logged_once(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel())
        fleet.model.available = False
        a, b = fleet.add("one"), fleet.add("two")
        fleet.turn(a, b)
        fleet.turn(a, b)
        assert sum("no model is configured" in m for m in fleet.log) == 1

    def test_children_are_named_with_their_siblings_in_view(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("auth-ui")))
        lead = fleet.add("auth-lead", source="human")
        fleet.add("auth-db", source="parent", parent=lead)
        c = fleet.add("child-3", parent=lead)
        fleet.turn(c)
        fleet.turn(c)
        prompt = fleet.model.prompts[0]
        assert "its parent: auth-lead" in prompt
        assert "## Its siblings (children of the same parent):\nauth-db" in prompt
        assert "- auth-db [parent] repo overcode, child of auth-lead" in prompt
        assert fleet.get(c).name == "auth-ui"


class TestChoosing:

    def test_taken_candidates_are_skipped(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("wiki1", "wiki2")))
        fleet.add("wiki1", source="human")
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "wiki2"  # a numbered series is legal

    def test_all_taken_gets_the_shortest_disambiguator(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("wiki1", "wiki2")))
        fleet.add("wiki1", source="human")
        fleet.add("wiki2", source="human")
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "wiki3"

    def test_an_alias_is_taken_and_candidates_are_cleaned(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("Old Name", "JWT Refresh!")))
        other = fleet.add("holder", source="human")
        fleet.sm.rename_session(other.id, "holder2")
        fleet.sm.rename_session(other.id, "old-name")  # holder, holder2 now aliases
        fleet.sm.rename_session(other.id, "holder3")
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "JWT-Refresh"     # "Old-Name": old-name, any case

    def test_the_advice_decides_case(self, tmp_path):
        model = StubModel(_reply("AUTH-REFRESH"))
        fleet = Fleet(tmp_path, cfg=_cfg(advice="UPPERCASE, words joined by -"), model=model)
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "AUTH-REFRESH"
        assert "UPPERCASE" in model.prompts[0] and "lowercase" not in model.prompts[0]

    def test_a_configured_rule_drops_a_candidate(self, tmp_path):
        fleet = Fleet(tmp_path, cfg=_cfg(rules={"forbid": ["tmp"]}),
                      model=StubModel(_reply("tmp-fix", "auth-fix")))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "auth-fix"

    def test_max_length_is_a_target_not_a_rule(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("a-rather-long-descriptive-name")))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "a-rather-long-descriptive-name"
        assert "At most 18 characters" in fleet.model.prompts[0]

    def test_a_lost_race_chooses_again(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix", "auth-fix")))
        a = fleet.add("work")
        b = fleet.add("racer", source="human")

        def complete(prompt, **kw):
            fleet.sm.rename_session(b.id, "jwt-fix")  # another agent takes it first
            return json.dumps(_reply("jwt-fix", "auth-fix"))
        fleet.model.complete = complete
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "auth-fix"

    def test_a_kept_default_name_is_asked_again_three_times_at_most(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(*[_reply("work")] * 5))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert len(fleet.model.prompts) == 1 and not fleet.state(a).first_done
        for _ in range(20):
            fleet.idle(a, seconds=300)
        assert len(fleet.model.prompts) == 3 and fleet.state(a).first_done
        assert fleet.renames == []

    def test_a_kept_auto_name_is_not_asked_again(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(*[_reply("work")] * 3))
        a = fleet.add("work", source="auto")
        fleet.turn(a)
        fleet.turn(a)
        for _ in range(5):
            fleet.idle(a, seconds=700)
        assert len(fleet.model.prompts) == 1 and fleet.state(a).first_done


class TestSpend:

    def test_a_call_is_a_ledger_row_under_overcode(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix"), tokens=(10_000, 100)))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        rows = [r for r in fleet.sm.ledger.iter_rows() if r.get("namer")]
        assert len(rows) == 1
        row = rows[0]
        assert row["billing_code"] == "overcode" and row["code"] == fleet.sm.ledger.codes.find("overcode")
        assert row["parent"] == a.id and row["kind"] == "final"
        assert row["tok_in"] == 10_000 and row["tok_out"] == 100 and row["cost"] > 0
        report = fleet.sm.ledger.named(fleet.sm.ledger.totals(save=False))
        assert report["overcode"]["cost"] == pytest.approx(row["cost"])

    def test_the_daily_cap_stops_calls_until_tomorrow(self, tmp_path):
        fleet = Fleet(tmp_path, cfg=_cfg(cost_cap_per_day=0.001),
                      model=StubModel(_reply("one-fix"), _reply("two-fix"), _reply("three-fix"),
                                      tokens=(100_000, 1000)))
        a, b = fleet.add("one"), fleet.add("two")
        fleet.turn(a)
        fleet.turn(a)                       # this call spends past the cap
        fleet.turn(b)
        fleet.turn(b)
        assert len(fleet.model.prompts) == 1 and fleet.get(b).name == "two"
        assert any("cost cap" in m for m in fleet.log)
        fleet.clock.now += timedelta(days=1)
        fleet.turn(b)
        assert fleet.get(b).name == "two-fix"

    def test_no_tokens_no_row(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("x-name"), tokens=(0, 0)))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert not [r for r in fleet.sm.ledger.iter_rows() if r.get("namer")]


class TestBillingCode:

    def test_a_root_agents_own_code_follows_its_auto_names(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix"), _reply("db-move")))
        a = fleet.add("work")
        codes = fleet.sm.ledger.codes
        code = codes.resolve("work", root=a.id)
        fleet.sm.update_session(a.id, billing_code_id=code, billing_code="work")
        fleet.turn(a)
        fleet.turn(a)
        assert codes.name_of(code) == "jwt-fix"
        for _ in range(20):
            fleet.turn(a)
        assert fleet.get(a).name == "db-move"
        assert codes.name_of(code) == "db-move"  # later auto renames too
        assert codes.get(code)["source"] == "default"

    def test_a_code_renamed_by_hand_stops_following(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix"), _reply("db-move")))
        a = fleet.add("work")
        codes = fleet.sm.ledger.codes
        code = codes.resolve("work", root=a.id)
        fleet.sm.update_session(a.id, billing_code_id=code, billing_code="work")
        fleet.turn(a)
        fleet.turn(a)
        codes.rename(code, "auth")
        for _ in range(20):
            fleet.turn(a)
        assert fleet.get(a).name == "db-move" and codes.name_of(code) == "auth"

    def test_folder_and_default_codes_never_follow(self, tmp_path):
        # billing.new_agent_code folder / default resolve as human codes
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix"), _reply("ui-fix")))
        codes = fleet.sm.ledger.codes
        a, b = fleet.add("work"), fleet.add("other")
        folder = codes.resolve("myrepo", source="human", root=a.id)
        general = codes.resolve("general", source="human", root=b.id)
        fleet.sm.update_session(a.id, billing_code_id=folder, billing_code="myrepo")
        fleet.sm.update_session(b.id, billing_code_id=general, billing_code="general")
        fleet.turn(a, b)
        fleet.turn(a, b)
        assert {fleet.get(a).name, fleet.get(b).name} == {"jwt-fix", "ui-fix"}
        assert codes.name_of(folder) == "myrepo" and codes.name_of(general) == "general"

    def test_a_human_code_and_a_childs_code_stay(self, tmp_path):
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix"), _reply("ui-fix")))
        codes = fleet.sm.ledger.codes
        a = fleet.add("work")
        human = codes.resolve("team-x", source="human", root=a.id)
        fleet.sm.update_session(a.id, billing_code_id=human, billing_code="team-x")
        lead = fleet.add("lead", source="human")
        shared = codes.resolve("lead", root=lead.id)
        c = fleet.add("kid", parent=lead)
        fleet.sm.update_session(c.id, billing_code_id=shared, billing_code="lead")
        fleet.turn(a, c)
        fleet.turn(a, c)
        assert fleet.get(a).name == "jwt-fix" and fleet.get(c).name == "ui-fix"
        assert codes.name_of(human) == "team-x" and codes.name_of(shared) == "lead"


# ── inputs, prompt and reply ─────────────────────────────────────────


class TestInputs:

    def test_the_prompt_carries_every_input(self, tmp_path):
        fleet = Fleet(tmp_path)
        lead = fleet.add("lead", source="human")
        a = fleet.add("work", parent=lead)
        fleet.sm.update_session(a.id, branch="fix-jwt", pr_number=542, start_directory="/repo")
        with open(agent_file("agents", a.id, "prompts"), "w") as f:
            f.write(json.dumps({"t": 1, "prompt": "also add tests"}) + "\n")
        with open(agent_file("agents", a.id, "hook_events"), "w") as f:
            for path in ["/repo/auth/session.py"] * 3 + ["/repo/auth/jwt.py"]:
                f.write(json.dumps({"event": "PostToolUse", "tool_name": "Edit",
                                    "tool_input": {"file_path": path}}) + "\n")
            f.write(json.dumps({"event": "PostToolUse", "tool_name": "Read",
                                "tool_input": {"file_path": "/repo/README.md"}}) + "\n")
        archived = [{"id": "gone", "name": "old-wiki", "name_source": "auto",
                     "repo_name": "docs"}]
        session = fleet.get(a)
        inputs = nm.gather_inputs(session, tmux_session="agents", sessions=fleet.sm.list_sessions(),
                                  archived=archived, max_length=16)
        from overcode.summarizer_prompts import DEFAULT_NAMING_PROMPT
        prompt = nm.render(inputs, DEFAULT_NAMING_PROMPT, "kebab-case please")
        assert "kebab-case please" in prompt
        assert "Fix the JWT refresh race" in prompt
        assert "1. also add tests" in prompt
        assert "repo overcode, branch fix-jwt (PR #542)" in prompt
        assert "Files it edits most: auth/session.py, auth/jwt.py" in prompt
        assert "README" not in prompt
        assert "Its name now: work (default); its parent: lead" in prompt
        assert "- lead [human] repo overcode" in prompt
        assert "- old-wiki [auto] repo docs (ended)" in prompt
        assert "At most 16 characters" in prompt
        assert "(none)" not in prompt.split("## Its siblings")[0]

    def test_a_hookless_agent_falls_back_to_its_pane(self, tmp_path):
        fleet = Fleet(tmp_path)
        a = fleet.add("work", brief="")
        pane = "\n".join(f"line {i}" for i in range(100))
        inputs = nm.gather_inputs(fleet.get(a), tmux_session="agents",
                                  sessions=fleet.sm.list_sessions(), pane=lambda: pane)
        assert inputs.pane.splitlines() == [f"line {i}" for i in range(40, 100)]
        assert "last lines of its terminal" in nm.render(inputs, "{prompts}", "")
        # With a brief, the pane is not read
        b = fleet.add("other")
        read = []
        nm.gather_inputs(fleet.get(b), tmux_session="agents", sessions=[],
                         pane=lambda: read.append(1) or "")
        assert read == []

    def test_sister_agents_are_not_in_the_fleet(self, tmp_path):
        local = SimpleNamespace(id="a", name="a", name_source="auto", repo_name=None,
                                parent_session_id=None, is_remote=False)
        sister = SimpleNamespace(id="b", name="b", name_source="auto", repo_name=None,
                                 parent_session_id=None, is_remote=True)
        assert [e.name for e in nm.fleet_entries([local, sister])] == ["a"]

    def test_recent_archived_is_the_last_day(self, tmp_path):
        recs = [{"id": "1", "name": "new", "end_time": (T0 - timedelta(hours=2)).isoformat()},
                {"id": "2", "name": "old", "end_time": (T0 - timedelta(hours=30)).isoformat()}]
        sm = SimpleNamespace(iter_archived_records=lambda since, newest_first: iter(recs))
        assert [r["name"] for r in nm.recent_archived(sm, T0)] == ["new"]


class TestParseReply:

    @pytest.mark.parametrize("text,want", [
        ('{"candidates": ["a-b", "c"], "keep": false, "why": "x"}', (["a-b", "c"], False, "x")),
        ('```json\n{"candidates": ["a"], "keep": "true"}\n```', (["a"], True, "")),
        ('Sure! {"candidates": "solo"} hope that helps', (["solo"], False, "")),
        ('{"keep": true, "why": "fits"}', ([], True, "fits")),
        ('{"candidates": [1, " ", "ok"], "why": 3}', (["ok"], False, "")),
    ])
    def test_lenient(self, text, want):
        reply = nm.parse_reply(text)
        assert (reply.candidates, reply.keep, reply.why) == want

    @pytest.mark.parametrize("text", [None, "", "no json", "{broken", '["a"]',
                                      '{"candidates": []}'])
    def test_unreadable(self, text):
        assert nm.parse_reply(text) is None


# ── notices (§8) ─────────────────────────────────────────────────────


def _launcher(tmp_path):
    sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
    return AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), sm), sm


class TestNotices:

    def test_rename_stores_the_source_and_tells_agent_and_parent(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        parent = launcher.launch(name="lead")
        child = launcher.launch(name="overcode-main2")
        sm.update_session(child.id, parent_session_id=parent.id, name_source="default")

        assert launcher.rename(sm.get_session(child.id), "jwt-refresh", source="auto")

        got = sm.get_session(child.id)
        assert got.name == "jwt-refresh" and got.name_source == "auto"
        assert "renamed from 'overcode-main2' to 'jwt-refresh'" in agent_file(
            "agents", child.id, "rename_notice").read_text()
        notice = agent_file("agents", parent.id, "child_notice").read_text()
        assert notice == ("overcode: your child agent 'overcode-main2' is now called "
                          "'jwt-refresh'; the old name still reaches it.\n")

    def test_notices_never_look_like_json(self, tmp_path):
        """codex reads hook stdout starting with [ or { as JSON: a
        "[overcode] ..." notice failed its prompt hook and never arrived."""
        launcher, sm = _launcher(tmp_path)
        parent = launcher.launch(name="lead")
        child = launcher.launch(name="old-name")
        sm.update_session(child.id, parent_session_id=parent.id)
        assert launcher.rename(sm.get_session(child.id), "new-name")
        for sid, kind in ((child.id, "rename_notice"), (parent.id, "child_notice")):
            text = agent_file("agents", sid, kind).read_text()
            assert text.strip() and text.lstrip()[0] not in "[{", text

    def test_no_source_leaves_the_record_alone(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        a = launcher.launch(name="alpha")
        sm.update_session(a.id, name_source="default")
        launcher.rename(sm.get_session(a.id), "beta")
        assert sm.get_session(a.id).name_source == "default"

    def test_the_parent_reads_the_notice_once(self, tmp_path, monkeypatch, capsys):
        from overcode import hook_handler

        launcher, sm = _launcher(tmp_path)
        parent = launcher.launch(name="lead")
        agent_file("agents", parent.id, "child_notice").write_text("[overcode] Your child X\n")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "lead")
        monkeypatch.setenv("OVERCODE_SESSION_ID", parent.id)
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        for _ in range(2):
            monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
                "hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "hi"})))
            hook_handler.handle_hook_event()
        assert capsys.readouterr().out.count("Your child X") == 1

    def test_tui_toast_for_auto_renames_only(self):
        from overcode.name_source import auto_rename_toast
        before = SimpleNamespace(name="overcode-main2", name_source="default")
        assert auto_rename_toast(before, SimpleNamespace(name="jwt-fix", name_source="auto")) == \
            "overcode-main2 → jwt-fix (auto)"
        assert auto_rename_toast(before, SimpleNamespace(name="jwt-fix", name_source="human")) is None
        assert auto_rename_toast(before, SimpleNamespace(name="overcode-main2",
                                                         name_source="auto")) is None


# ── capture (§4) ─────────────────────────────────────────────────────


class TestCapture:

    def test_launch_writes_the_brief(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        launcher._deliver_initial_prompt = lambda *a: None  # no CLI to wait for
        a = launcher.launch(name="alpha", initial_prompt="é" * 3000)
        brief = agent_file("agents", a.id, "brief").read_text(encoding="utf-8")
        assert brief == "é" * 1024  # 2 KB, cut on a character
        b = launcher.launch(name="beta")
        assert not agent_file("agents", b.id, "brief").exists()

    def test_a_fork_takes_its_sources_brief(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        launcher._deliver_initial_prompt = lambda *a: None
        a = launcher.launch(name="alpha", initial_prompt="the task")
        sm.update_session(a.id, active_agent_session_id="11111111-2222-4333-8444-555555555555")
        fork = launcher.launch_fork("alpha-fork", sm.get_session(a.id))
        assert agent_file("agents", fork.id, "brief").read_text() == "the task"

    def test_the_first_three_prompts_are_kept(self, tmp_path, monkeypatch):
        from overcode import hook_handler

        sid = "sid-1"
        agent_dir("agents", sid).mkdir(parents=True)
        monkeypatch.setenv("OVERCODE_SESSION_ID", sid)
        for text in ["one", "", "two", "x" * 5000, "four"]:
            hook_handler._capture_prompt("agents", text)
        assert nm.read_prompts("agents", sid) == ["one", "two", "x" * 1024]

    def test_files_are_bundled(self, tmp_path):
        from overcode.agent_bundle import collect_files
        d = agent_dir("agents", "sid-1")
        d.mkdir(parents=True)
        (d / "brief.txt").write_text("b")
        (d / "prompts.jsonl").write_text('{"prompt": "p"}\n')
        (d / "namer.json").write_text('{"renames": 1}')
        files = collect_files(d)
        assert files["brief.txt"] == "b" and files["namer.json"] == {"renames": 1}
        assert "prompts.jsonl" in files


# ── the first name (first_after.minutes after the first prompt) ───────


class TestFirstName:

    def test_named_mid_turn_without_waiting_for_its_stop(self, tmp_path):
        model = StubModel(_reply("jwt-fix"))
        fleet = Fleet(tmp_path, cfg=_cfg(first_after={"minutes": 5}), model=model)
        a = fleet.add("overcode-main2")
        fleet.clock.now += timedelta(minutes=4, seconds=59)
        fleet.green[a.id] = 299
        fleet.idle(a, status="running")
        assert model.prompts == []
        fleet.green[a.id] = 301
        fleet.idle(a, seconds=1, status="running")
        assert fleet.get(a).name == "jwt-fix" and fleet.get(a).name_source == "auto"

    def test_off_or_locked_is_never_named(self, tmp_path):
        model = StubModel(_reply("jwt-fix"))
        fleet = Fleet(tmp_path, cfg=_cfg(enabled=False), model=model)
        a = fleet.add("overcode-main2")
        fleet.turn(a, minutes=30)
        fleet.cfg = _cfg()
        b = fleet.add("mine", source="human")
        fleet.turn(b, minutes=30)
        assert model.prompts == []

    def test_claude_title_waits_until_it_is_idle(self, tmp_path):
        mock = MockTmux()
        fleet = Fleet(tmp_path, model=StubModel(_reply("jwt-fix")), tmux=mock)
        a = fleet.add("work")
        mock.set_pane_content("agents", "work-w", EMPTY)  # a working Claude's box is empty too
        fleet.clock.now += timedelta(minutes=5)
        fleet.green[a.id] = 300
        fleet.idle(a, status="running")
        assert fleet.get(a).name == "jwt-fix"
        assert mock.sent_keys == []
        fleet.idle(a, seconds=5)
        assert [k[2] for k in mock.sent_keys] == ["/rename jwt-fix"]

    def test_handed_back_it_is_named_afresh(self, tmp_path):
        model = StubModel(_reply("jwt-fix"), _reply("db-migrate"))
        fleet = Fleet(tmp_path, model=model)
        a = fleet.add("work")
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "jwt-fix"
        fleet.sm.update_session(a.id, name_source="human")       # Ctrl+N: kept
        nm.hand_to_namer(fleet.sm, fleet.get(a), "agents")       # then handed back
        got = fleet.get(a)
        assert got.name_source == "default" and not fleet.state(a).first_done
        fleet.idle(a, seconds=61)
        assert fleet.get(a).name == "db-migrate"




# ── Claude's own session name (§7) ───────────────────────────────────


EMPTY = "  some output\n❯ \n──────\n  ⏵⏵ auto mode on"
TYPED = "  some output\n❯ half a thought\n──────"


class TestClaudeTitle:

    def _fleet(self, tmp_path, **kw):
        mock = MockTmux()
        fleet = Fleet(tmp_path, tmux=mock, **kw)
        return fleet, mock

    def test_launch_passes_name(self):
        from overcode.backends import get_backend
        from overcode.backends.base import LaunchSpec
        cmd = get_backend("claude-code").build_command(LaunchSpec(name="jwt-fix"))
        assert cmd[cmd.index("--name") + 1] == "jwt-fix"
        for backend in ("codex", "grok", "hermes", "opencode", "shell"):
            assert get_backend(backend).display_name_args("x") == []

    def test_an_auto_rename_is_typed_in_when_the_input_is_empty(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, model=StubModel(_reply("jwt-fix")))
        a = fleet.add("work")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.turn(a)
        fleet.turn(a)
        assert ("agents", "work-w", "/rename jwt-fix", True) in mock.sent_keys
        assert fleet.state(a).cli_title == "jwt-fix"

    def test_typed_text_waits_for_the_next_stop(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.turn(a)                                   # title known: "work"
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", TYPED)
        fleet.turn(a)
        assert not [k for k in mock.sent_keys if k[2].startswith("/rename")]
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.turn(a)
        assert [k[2] for k in mock.sent_keys] == ["/rename renamed"]
        fleet.turn(a)
        assert len(mock.sent_keys) == 1                 # aligned: once

    def test_an_idle_agent_takes_a_rename_without_a_turn(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.turn(a)                                   # title known: "work"
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a)
        assert [k[2] for k in mock.sent_keys] == ["/rename renamed"]
        fleet.idle(a, seconds=60)
        assert len(mock.sent_keys) == 1                 # aligned: once

    def test_tui_keys_do_not_hold_up_the_title(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False), keypress_age=lambda: 1.0)
        a = fleet.add("work")
        fleet.turn(a)
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a)                                   # the rename was just typed in the TUI
        assert [k[2] for k in mock.sent_keys] == ["/rename renamed"]

    def test_a_sleeping_agent_takes_a_rename(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a, status="asleep")
        assert [k[2] for k in mock.sent_keys] == ["/rename renamed"]

    def test_an_idle_agent_with_typed_input_is_retried(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", TYPED)
        fleet.idle(a)
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a, seconds=5)
        assert mock.sent_keys == []                     # not before the retry
        fleet.idle(a, seconds=nm.TITLE_RETRY_SECONDS)
        assert [k[2] for k in mock.sent_keys] == ["/rename renamed"]

    def test_a_busy_agent_is_not_retitled_between_turns(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.turn(a)
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a, status="running")
        fleet.idle(a, status="waiting_approval")
        assert mock.sent_keys == []

    def test_an_untitled_idle_agent_is_left_alone(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")                           # launched with --name work
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a)
        assert mock.sent_keys == [] and fleet.state(a).cli_title == "work"

    def test_a_launch_records_its_name_as_the_title(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        a = launcher.launch(name="alpha")
        assert nm.load_state("agents", a.id).cli_title == "alpha"
        assert launcher.rename(sm.get_session(a.id), "beta")
        assert nm.load_state("agents", a.id).cli_title == "alpha"  # still what Claude shows
        assert launcher.restart(sm.get_session(a.id))
        assert nm.load_state("agents", a.id).cli_title == "beta"   # relaunched with --name beta

    def test_a_rename_records_the_title_it_leaves_behind(self, tmp_path):
        launcher, sm = _launcher(tmp_path)
        a = launcher.launch(name="alpha")
        nm.save_state("agents", a.id, nm.NamerState())  # launched before titles were kept
        assert launcher.rename(sm.get_session(a.id), "beta")
        assert nm.load_state("agents", a.id).cli_title == "alpha"
        assert launcher.rename(sm.get_session(a.id), "gamma")
        assert nm.load_state("agents", a.id).cli_title == "alpha"

    def _renamer(self, tmp_path, status="waiting_user", pane=EMPTY, backend=None):
        launcher, sm = _launcher(tmp_path)
        a = launcher.launch(name="alpha", **({"backend": backend} if backend else {}))
        launcher._live_status = lambda session: status
        launcher.tmux.capture_pane = lambda window, lines=100: pane
        mock = launcher.tmux.client
        mock.sent_keys.clear()
        return launcher, sm, a, mock

    def _typed(self, mock):
        return [k[2] for k in mock.sent_keys if k[2].startswith("/rename")]

    def test_a_rename_types_the_title_in_at_once(self, tmp_path):
        launcher, sm, a, mock = self._renamer(tmp_path)
        assert launcher.rename(sm.get_session(a.id), "beta")
        assert self._typed(mock) == ["/rename beta"]
        assert nm.load_state("agents", a.id).cli_title == "beta"

    def test_an_asleep_agent_is_retitled_at_once(self, tmp_path):
        launcher, sm, a, mock = self._renamer(tmp_path, status="asleep")
        assert launcher.rename(sm.get_session(a.id), "beta")
        assert self._typed(mock) == ["/rename beta"]

    def test_a_busy_or_drafting_agent_is_left_to_the_namer(self, tmp_path):
        for i, kw in enumerate(({"status": "running"}, {"status": "waiting_approval"},
                                {"pane": TYPED})):
            launcher, sm, a, mock = self._renamer(tmp_path / str(i), **kw)
            assert launcher.rename(sm.get_session(a.id), "beta")
            assert self._typed(mock) == [], kw
            assert nm.load_state("agents", a.id).cli_title == "alpha", kw  # the namer's job now

    def test_title_lag_is_what_claude_still_shows(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        assert fleet.namer.title_lag(fleet.get(a)) is None      # never titled: --name work
        fleet.turn(a)
        fleet.sm.rename_session(a.id, "renamed", name_source="human")
        assert fleet.namer.title_lag(fleet.get(a)) == "work"
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.idle(a)
        assert fleet.namer.title_lag(fleet.get(a)) is None      # /rename typed in
        b = fleet.add("other", backend="codex")
        fleet.sm.rename_session(b.id, "other2", name_source="human")
        assert fleet.namer.title_lag(fleet.get(b)) is None      # no CLI title to lag
        fleet.cfg = _cfg(enabled=False, cli_title=False)
        fleet.sm.rename_session(a.id, "again", name_source="human")
        assert fleet.namer.title_lag(fleet.get(a)) is None

    def test_cli_title_off_types_nothing(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(cli_title=False), model=StubModel(_reply("x-y")))
        a = fleet.add("work")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.turn(a)
        fleet.turn(a)
        assert fleet.get(a).name == "x-y" and mock.sent_keys == []

    def test_other_backends_are_not_typed_into(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, model=StubModel(_reply("x-y")))
        a = fleet.add("work", backend="codex")
        mock.set_pane_content("agents", "work-w", EMPTY)
        fleet.turn(a)
        fleet.turn(a)
        assert mock.sent_keys == []

    def test_a_rename_typed_in_claude_is_a_human_rename(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.titles[a.id] = "My New Task"
        fleet.turn(a)
        got = fleet.get(a)
        assert got.name == "my-new-task" and got.name_source == "human"
        assert fleet.renames == [("work", "my-new-task", "human")]
        fleet.turn(a)
        fleet.turn(a)
        assert len(fleet.renames) == 1                  # not adopted twice

    def test_overcodes_own_titles_are_not_adopted(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        a = fleet.add("work")
        fleet.sm.rename_session(a.id, "newer", name_source="auto")
        fleet.titles[a.id] = "work"                     # --name at launch: an old name
        fleet.turn(a)
        fleet.titles[a.id] = "newer"                    # overcode's own /rename
        fleet.turn(a)
        assert fleet.renames == []

    def test_a_title_taken_by_another_agent_is_not_adopted(self, tmp_path):
        fleet, mock = self._fleet(tmp_path, cfg=_cfg(enabled=False))
        fleet.add("taken", source="human")
        a = fleet.add("work")
        fleet.titles[a.id] = "taken"
        fleet.turn(a)
        assert fleet.get(a).name == "work"
        assert any("not adopted" in m for m in fleet.log)

    def test_input_line_empty(self):
        from overcode.backends import get_backend
        claude = get_backend("claude-code")
        assert nm.input_line_empty(EMPTY, claude)
        assert not nm.input_line_empty(TYPED, claude)
        assert not nm.input_line_empty("no prompt here", claude)
        assert not nm.input_line_empty("❯ \nscrollback\n❯ typed", claude)

    def test_transcripts_carry_the_custom_title(self, tmp_path):
        from overcode.transcript_index import TranscriptIndex
        path = tmp_path / "t.jsonl"
        lines = [{"type": "custom-title", "customTitle": "first", "sessionId": "s"},
                 {"type": "agent-name", "agentName": "first", "sessionId": "s"},
                 {"type": "custom-title", "customTitle": "second", "sessionId": "s"}]
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        totals, _ = TranscriptIndex(str(path)).stats()
        assert totals["custom_title"] == "second"


# ── the daemon and the CLI ───────────────────────────────────────────


class TestWiring:

    def test_the_daemon_has_a_namer_on_its_state_dir(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        daemon = MonitorDaemon.for_test(tmp_path / "state")
        assert daemon.namer.spend.path == tmp_path / "state" / "namer_spend.json"
        daemon.stats_sync.transcript_titles["x"] = "t"
        assert daemon.namer._titles() == {"x": "t"}

    def test_name_suggest_prints_without_renaming(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app

        monkeypatch.setattr("overcode.cli.split.open_split", lambda *a, **k: None)
        sm = SessionManager(skip_git_detection=True)
        s = sm.create_session(name="work", tmux_session="agents", tmux_window="work-w",
                              command=["claude"], name_source="default")
        agent_dir("agents", s.id).mkdir(parents=True)
        agent_file("agents", s.id, "brief").write_text("fix the cache")
        model = StubModel(_reply("cache-fix", "cache-bug", why="the brief"))
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)
        advice = tmp_path / "advice.md"
        advice.write_text("ADVICE-B")

        result = CliRunner().invoke(app, ["name", "suggest", "work", "--json",
                                          "--advice-file", str(advice)])
        assert result.exit_code == 0, result.output
        out = json.loads(result.output.strip().splitlines()[-1])
        assert out["pick"] == "cache-fix" and out["candidates"] == ["cache-fix", "cache-bug"]
        assert out["why"] == "the brief" and out["advice"] == "ADVICE-B"
        assert "ADVICE-B" in model.prompts[0]
        assert SessionManager(skip_git_detection=True).get_session(s.id).name == "work"

    def test_name_suggest_reports_a_missing_model(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app

        sm = SessionManager(skip_git_detection=True)
        sm.create_session(name="work", tmux_session="agents", tmux_window="work-w",
                          command=["claude"])
        model = StubModel()
        model.available = False
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)
        result = CliRunner().invoke(app, ["name", "suggest", "work"])
        assert result.exit_code == 1 and "no model" in result.output

    def test_list_self_finds_the_agent_by_id(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app

        sm = SessionManager(skip_git_detection=True)
        me = sm.create_session(name="renamed-me", tmux_session="agents", tmux_window="w1",
                               command=["claude"])
        kid = sm.create_session(name="kid", tmux_session="agents", tmux_window="w2",
                                command=["claude"])
        sm.update_session(kid.id, parent_session_id=me.id)
        sm.create_session(name="stranger", tmux_session="agents", tmux_window="w3",
                          command=["claude"])
        monkeypatch.setattr("overcode.cli.agent._reconcile_without_daemon", lambda *a: None)
        monkeypatch.setenv("OVERCODE_SESSION_ID", me.id)
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "launch-name")
        result = CliRunner().invoke(app, ["list", "--self", "--json"])
        assert result.exit_code == 0, result.output
        names = {json.loads(line)["name"] for line in result.output.strip().splitlines()} \
            if not result.output.strip().startswith("[") else \
            {a["name"] for a in json.loads(result.output)}
        assert names == {"renamed-me", "kid"}

    def test_list_self_outside_an_agent(self, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app

        sm = SessionManager(skip_git_detection=True)
        sm.create_session(name="a", tmux_session="agents", tmux_window="w1", command=["claude"])
        monkeypatch.setattr("overcode.cli.agent._reconcile_without_daemon", lambda *a: None)
        result = CliRunner().invoke(app, ["list", "--self"])
        assert result.exit_code == 1 and "inside an overcode agent" in result.output
