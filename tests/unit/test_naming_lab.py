"""The naming lab (#542, auto-naming §9): the engine, the TUI dialog, `name lab`.

A stub model answers per agent (by the brief in its prompt), so the picks,
the rejected candidates, collisions, A/B and the spend are checked with no
network. Archived agents go through the real archive path
(``delete_session``), so their inputs come back from day-bundle members.
"""

import json
import os
import threading
from types import SimpleNamespace

import pytest
from rich.cells import cell_len
from rich.text import Text

from overcode import naming_lab as nl
from overcode import summarizer_prompts as sp
from overcode.paths import agent_dir, agent_file
from overcode.session_manager import SessionManager


def _cfg(**over):
    cfg = {"enabled": False, "advice": "", "max_length": 18, "rules": {},
           "first_after": {"turns": 2, "green_minutes": 3, "output_tokens": 15000},
           "max_renames": 2, "cost_cap_per_day": 0.50, "provider": None, "cli_title": True}
    cfg.update(over)
    return cfg


class StubModel:
    """The summarizer client: answers by the brief its prompt carries."""

    def __init__(self, answers, tokens=(1000, 40), model="gpt-4o-mini"):
        self.answers = answers         # brief substring -> reply dict
        self.prompts = []
        self.available = True
        self.model = model
        self.last_input_tokens, self.last_output_tokens = tokens
        self.lock = threading.Lock()

    def complete(self, prompt, max_tokens=150, timeout=15.0):
        with self.lock:
            self.prompts.append(prompt)
        for needle, reply in self.answers.items():
            if needle in prompt:
                return json.dumps(reply)
        return None


ANSWERS = {
    "JWT refresh": {"candidates": ["auth", "jwt-refresh"], "keep": False, "why": "the JWT work"},
    "wiki pages": {"candidates": ["wiki"], "keep": False, "why": "the wiki"},
    "docs site": {"candidates": ["wiki"], "keep": False, "why": "also a wiki"},
    "billing": {"candidates": ["billing-codes-dialog-in-the-tui"], "keep": False, "why": "long"},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    for var in list(os.environ):
        if var.startswith("OVERCODE_"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "home"))
    return tmp_path


def _add(sm, name, brief, *, source="default", tmux="agents", repo="overcode"):
    s = sm.create_session(name=name, tmux_session=tmux, tmux_window=f"{name}-w",
                          command=["claude"], name_source=source, start_directory="/tmp")
    sm.update_session(s.id, repo_name=repo, branch="main")
    agent_dir(tmux, s.id).mkdir(parents=True, exist_ok=True)
    if brief:
        agent_file(tmux, s.id, "brief").write_text(brief)
    return sm.get_session(s.id)


def _fleet(sm, tmux="agents"):
    return [
        _add(sm, "auth", "Fix the JWT refresh race", source="human", tmux=tmux),
        _add(sm, "proj", "Write the wiki pages", tmux=tmux),
        _add(sm, "proj2", "Build the docs site", tmux=tmux),
        _add(sm, "kid", "Add the billing dialog", source="parent", tmux=tmux),
        _add(sm, "quiet", "", tmux=tmux),
    ]


def _runner(model, spend=None):
    return nl.NamingLabRunner(make_client=lambda: model, spend=spend)


DRAFT = (sp.DEFAULT_NAMING_PROMPT, "kebab-case, 1-3 words")


# ── the engine ───────────────────────────────────────────────────────────

class TestEngine:
    def test_live_agents_picks_rejections_and_collisions(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        assert [a.name for a in agents] == ["auth", "proj", "proj2", "kid", "quiet"]
        assert [a.locked for a in agents] == [True, False, False, True, False]
        assert not agents[4].has_inputs
        model = StubModel(ANSWERS)
        runner = _runner(model)
        assert runner.run(agents, *DRAFT) == 4      # quiet has nothing to read
        picks = {a.name: runner.result(a, *DRAFT) for a in agents}
        # auth's own name is not taken from itself; jwt-refresh never reached
        assert picks["auth"].pick == "auth"
        assert picks["proj"].pick == "wiki" and picks["proj2"].pick == "wiki"
        assert picks["quiet"] is None
        assert nl.collided(agents, [picks[a.name] for a in agents]) == {
            agents[1].id, agents[2].id}
        st = nl.stats(agents, [picks[a.name] for a in agents])
        assert st.changed == 2 and st.collisions == 2 and st.done == 4

    def test_rejected_says_why(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        model = StubModel({"wiki pages": {"candidates": ["proj2", "auth", "Wiki Docs"]}})
        runner = _runner(model)
        runner.run(agents[1:2], *DRAFT)
        p = runner.result(agents[1], *DRAFT)
        assert p.pick == "Wiki-Docs"
        assert p.rejected == [("proj2", "taken by agent 'proj2'"), ("auth", "taken by agent 'auth'")]

    def test_rules_name_the_rejection(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg(rules={"max_length": 12}))
        runner = _runner(StubModel({"billing": {"candidates": ["billing-dialog-tui", "billing"]}}))
        runner.run(agents[3:4], *DRAFT)
        p = runner.result(agents[3], *DRAFT)
        assert p.pick == "billing" and p.rejected == [("billing-dialog-tui", "too long")]

    def test_only_what_changed_is_run_again(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        model = StubModel(ANSWERS)
        runner = _runner(model)
        runner.run(agents, *DRAFT)
        assert runner.run(agents, *DRAFT) == 0 and len(model.prompts) == 4
        other = (DRAFT[0], "one word only")
        assert runner.run(agents, *other) == 4
        # Back to the first draft: still cached (A/B costs nothing to show)
        assert runner.run(agents, *DRAFT) == 0 and runner.calls == 8

    def test_a_failed_call_is_retried(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        model = StubModel({})
        runner = _runner(model)
        runner.run(agents[1:2], *DRAFT)
        assert runner.result(agents[1], *DRAFT).error == "no reply from the model"
        model.answers = ANSWERS
        assert runner.run(agents[1:2], *DRAFT) == 1
        assert runner.result(agents[1], *DRAFT).pick == "wiki"

    def test_the_advice_reaches_the_prompt_with_the_repos_file_after_it(self, home, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".overcode").mkdir()
        (repo / ".overcode" / "naming.md").write_text("prefix web- for frontend work")
        sm = SessionManager(skip_git_detection=True)
        s = _add(sm, "proj", "Write the wiki pages")
        sm.update_session(s.id, start_directory=str(repo))
        agents = nl.load_live(sm, "agents", _cfg())
        model = StubModel(ANSWERS)
        _runner(model).run(agents, sp.DEFAULT_NAMING_PROMPT, "DRAFT ADVICE")
        prompt = model.prompts[0]
        assert prompt.index("DRAFT ADVICE") < prompt.index("prefix web- for frontend work")

    def test_archived_agents_come_back_from_their_bundles(self, home):
        sm = SessionManager(skip_git_detection=True)
        fleet = _fleet(sm)
        agent_file("agents", fleet[1].id, "prompts").write_text(
            json.dumps({"prompt": "also the glossary"}) + "\n")
        for s in fleet[:3]:
            sm.delete_session(s.id)
        live = _add(sm, "still-here", "Write the wiki pages")
        agents = nl.load_archived(sm, "agents", _cfg(), limit=10)
        assert [a.name for a in agents] == ["proj2", "proj", "auth"]   # newest first
        assert all(a.archived and a.has_inputs for a in agents)
        assert agents[1].inputs.prompts == ["also the glossary"]
        assert agents[1].inputs.brief == "Write the wiki pages"
        # They are checked against each other, not the live fleet
        assert "proj2" in agents[1].ctx.taken and live.name not in agents[1].ctx.taken
        runner = _runner(StubModel(ANSWERS))
        runner.run(agents, *DRAFT)
        assert runner.result(agents[1], *DRAFT).pick == "wiki"
        assert nl.load_archived(sm, "agents", _cfg(), limit=1)[0].name == "proj2"

    def test_an_archived_agent_without_a_bundle_is_shown_not_run(self, home):
        from overcode.namer import bundle_files, gather_inputs
        s = SimpleNamespace(id="x", name="old", name_source="auto", parent_session_id=None,
                            repo_name=None, branch=None, pr_number=None, start_directory=None)
        inputs = gather_inputs(s, tmux_session="agents", sessions=[], files=bundle_files(None))
        assert not (inputs.brief or inputs.prompts or inputs.files)

    def test_bundle_files_read_hook_events_for_edited_files(self):
        from overcode.namer import bundle_files, edited_files
        events = "\n".join(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"/r/{f}"}})
                           for f in ("a.py", "b.py", "a.py"))
        read = bundle_files({"files": {"hook_events.jsonl": events, "brief.txt": "B"}})
        assert read("brief") == "B" and read("prompts") == ""
        assert edited_files(read("hook_events").splitlines(), "/r") == ["a.py", "b.py"]


class TestSpend:
    def test_calls_are_ledger_rows_under_overcode_and_count_against_the_cap(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        spend = nl.LabSpend("agents", sm.ledger, _cfg(cost_cap_per_day=0.5))
        runner = _runner(StubModel(ANSWERS), spend)
        runner.run(agents, *DRAFT)
        rows = [r for r in sm.ledger.iter_rows() if r.get("namer")]
        assert len(rows) == 4
        code = sm.ledger.codes.resolve("overcode")
        assert {r.get("code") for r in rows} == {code}
        assert spend.daily.today(spend._now()) == pytest.approx(runner.spent_usd)
        assert runner.spent_usd > 0

    def test_no_calls_over_the_cap(self, home):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        agents = nl.load_live(sm, "agents", _cfg())
        spend = nl.LabSpend("agents", sm.ledger, _cfg(cost_cap_per_day=0.01))
        spend.daily.add(0.02, spend._now())
        model = StubModel(ANSWERS)
        runner = _runner(model, spend)
        assert runner.run(agents, *DRAFT) == 0
        assert model.prompts == [] and "cost cap" in runner.blocked


class TestAdviceFile:
    def test_saved_advice_stands_in_for_config_and_the_repo_file_follows(self, home, tmp_path):
        from overcode.naming_policy import load_advice
        assert load_advice("from config") == "from config"
        sp.save_naming_file("advice", "from the lab")
        assert load_advice("from config") == "from the lab"
        sp.reset_naming_file("advice")
        assert load_advice("from config") == "from config"
        sp.reset_naming_file("advice")  # idempotent

    def test_the_prompt_file_is_naming_md(self, home):
        path = sp.save_naming_file("prompt", "custom {advice}")
        assert path.name == "naming.md" and sp.load_naming_prompt() == "custom {advice}"
        with pytest.raises(ValueError):
            sp.naming_file_path("other")


# ── the dialog ───────────────────────────────────────────────────────────

def _agent(name, source="default", archived=False, brief="b"):
    from overcode.namer import NamerInputs
    from overcode.naming_policy import NamingContext
    s = SimpleNamespace(id=f"id-{name}", name=name, name_source=source, start_directory=None)
    return nl.LabAgent(s, NamerInputs(session_id=s.id, name=name, name_source=source, brief=brief),
                       NamingContext(), archived=archived)


def _pick(name, **kw):
    return nl.LabPick(pick=name, candidates=kw.pop("candidates", [name]), **kw)


class _FakeRunner:
    """Results by (agent name, advice), for drawing tests."""

    def __init__(self, results, spend=None):
        self.results = results
        self.spend = spend
        self.calls = 3
        self.spent_usd = 0.0123
        self.blocked = None
        self.busy = False

    def result(self, agent, template, advice):
        return self.results.get((agent.name, advice))


def _dialog(agents, results, pinned=None, inner=116, rows_focused=True):
    from overcode.tui_widgets.naming_lab import NamingLab
    lab = NamingLab(id="nl")
    lab._runner = _FakeRunner(results)
    lab.agents = agents
    lab._inner_width = inner
    lab._rows_h = len(agents)
    lab.pinned = pinned
    lab._draw = lambda: None
    lab.current = lambda: ("T", "B")
    lab.post_message = lambda m: None
    lab._rows_focused = lambda: rows_focused
    return lab


def _fleet_rows():
    agents = [_agent("auth", "human"), _agent("proj"), _agent("proj2"), _agent("kid", "parent"),
              _agent("quiet", brief=""), _agent("billing-agent-x")]
    results = {
        ("auth", "B"): _pick("auth-jwt"),
        ("proj", "B"): _pick("wiki", rejected=[("proj2", "taken by agent 'proj2'")]),
        ("proj2", "B"): _pick("wiki"),
        ("kid", "B"): _pick("billing"),
        ("billing-agent-x", "B"): _pick("billing-codes-dialog-tui"),
        ("proj", "A"): _pick("wiki-pages"),
        ("proj2", "A"): _pick("docs-site"),
    }
    return agents, results


def _line(lines, name):
    return next(line for line in lines if line[1:].split()[:1] == [name])


class TestDialogDrawing:
    def test_rows_show_source_lock_pick_rejections_and_width(self):
        agents, results = _fleet_rows()
        lab = _dialog(agents, results)
        lines = lab.render_rows().plain.split("\n")[:-2]
        auth = _line(lines, "auth")
        assert "human locked" in auth and "auth-jwt" in auth
        proj = _line(lines, "proj")
        assert "wiki" in proj and "same pick as proj2" in proj
        assert "proj2: taken by agent 'proj2'" in proj
        assert " 4/18" in proj
        long = _line(lines, "billing-agent-x")
        assert "24/18" in long
        quiet = _line(lines, "quiet")
        assert "no recorded brief or prompts" in quiet
        assert "2 collisions" in lines[0] and "3 changed" in lines[0]   # proj, proj2, billing-…

    def test_a_pinned_draft_shows_a_and_b(self):
        agents, results = _fleet_rows()
        lab = _dialog(agents, results, pinned=("T", "A"))
        lines = lab.render_rows().plain.split("\n")
        assert lines[0].count("changed") == 2 and "A 2 changed · 0 collisions" in lines[0]
        proj = _line(lines, "proj")
        assert proj.index("wiki-pages") < proj.index("|") < proj.index("wiki ")

    @pytest.mark.parametrize("cols", [120, 160, 200, 260])
    @pytest.mark.parametrize("state", ["plain", "pinned", "confirm", "empty", "nomodel"])
    def test_every_line_is_the_dialogs_width(self, cols, state):
        from overcode.tui_widgets.naming_lab import NamingLab
        agents, results = _fleet_rows()
        agents.append(_agent("a-very-long-agent-name-that-goes-on-and-on-" * 2))
        results[(agents[-1].name, "B")] = _pick(
            "x" * 40, rejected=[("y" * 50, "too long")] * 4, why="w" * 200)
        inner = min(NamingLab.WIDTH, cols - 2) - 4
        lab = _dialog([] if state == "empty" else agents, results,
                      pinned=("T", "A") if state == "pinned" else None, inner=inner)
        lab._available = state != "nomodel"
        lab._saved = "B"
        if state == "confirm":
            lab.selected = 1
            lab.action_apply()
            assert lab._confirm_apply == agents[1].id
        lines = lab.render_rows().plain.split("\n") + lab.render_head().plain.split("\n")
        assert all(cell_len(line) == inner for line in lines), [
            (cell_len(line), line) for line in lines if cell_len(line) != inner]

    def test_enter_asks_then_applies_and_archived_rows_cannot(self):
        agents, results = _fleet_rows()
        lab = _dialog(agents, results)
        applied = []
        lab._apply_fn = lambda agent, name: applied.append((agent.name, name))
        lab.selected = 1
        lab.action_apply()
        assert applied == [] and "as your choice (locked)?" in lab.render_rows().plain
        lab.action_close()               # Esc cancels the confirm, not the dialog
        assert lab._confirm_apply is None
        lab.action_apply()
        lab.action_apply()
        assert applied == [("proj", "wiki")]
        # A locked agent's pick can still be applied: it is a human choice
        lab.selected = 0
        lab.action_apply()
        lab.action_apply()
        assert applied[-1] == ("auth", "auth-jwt")
        lab.agents = [_agent("old", archived=True)]
        lab._runner.results[("old", "B")] = _pick("new")
        lab.selected = 0
        lab.action_apply()
        assert lab._confirm_apply is None and len(applied) == 2


# ── the dialog in the TUI ────────────────────────────────────────────────

from tests.tui_harness import ready as _ready, until as _until  # noqa: E402


@pytest.mark.asyncio
class TestLabInTUI:

    @pytest.fixture
    def stub(self, monkeypatch):
        model = StubModel(ANSWERS)
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)
        monkeypatch.setattr("overcode.config.get_naming_config", lambda: _cfg())
        return model

    def _seed(self):
        sm = SessionManager(skip_git_detection=True)
        return _fleet(sm, tmux="test")

    async def _open(self, pilot):
        from overcode.tui_widgets import NamingLab
        await _ready(pilot)
        pilot.app.action_open_naming_lab()
        lab = pilot.app.query_one("#naming-lab", NamingLab)
        await _until(pilot, lambda: lab.agents and not lab.loading)
        return lab

    async def test_opens_on_the_advice_runs_the_fleet_and_saves(self, make_tui, stub, tui_state_dir):
        self._seed()
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            assert lab.has_class("visible") and app.focused is lab.editor
            assert lab.kind == "advice" and lab.draft == sp.DEFAULT_NAMING_ADVICE
            await _until(pilot, lambda: lab._runner.calls >= 4)
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = lab.render_rows().plain
            assert "wiki" in text and "same pick as proj2" in text
            assert "naming today $" in text
            await pilot.press("ctrl+a")
            await pilot.press(*"q! ")
            await _until(pilot, lambda: lab.dirty)
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not lab.dirty
            saved = (tui_state_dir / "prompts" / "naming-advice.md").read_text()
            assert saved.startswith("q! lowercase kebab-case")

    async def test_enter_on_a_row_renames_it_as_a_human_choice(self, make_tui, stub, monkeypatch):
        fleet = self._seed()
        renames = []

        def rename(self, session, new_name, force=False, source="human"):
            renames.append((session.name, new_name, source))
            self.sessions.update_session(session.id, name=new_name, name_source=source)
            return True
        monkeypatch.setattr("overcode.launcher.AgentLauncher.rename", rename)
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            await _until(pilot, lambda: lab._runner.calls >= 4)
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+n")
            assert lab.rows_widget.has_focus
            await pilot.press("down")          # proj
            await pilot.press("enter")
            assert renames == []
            await pilot.press("enter")
            await _until(pilot, lambda: lab.agents[1].name == "wiki")
            assert renames == [("proj", "wiki", "human")]
            assert lab.agents[1].locked
            got = SessionManager(skip_git_detection=True).get_session(fleet[1].id)
            assert got.name == "wiki" and got.name_source == "human"

    async def test_a_applies_every_pick_and_names_people_chose_only_on_a_second_yes(
            self, make_tui, stub, monkeypatch):
        model = StubModel({**ANSWERS, "docs site": {"candidates": ["docs"], "keep": False}})
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)
        self._seed()
        renames = []

        def rename(self, session, new_name, force=False, source="human"):
            renames.append((session.name, new_name, source))
            self.sessions.update_session(session.id, name=new_name, name_source=source)
            return True
        monkeypatch.setattr("overcode.launcher.AgentLauncher.rename", rename)
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            await _until(pilot, lambda: lab._runner.calls >= 4)
            await app.workers.wait_for_complete()
            legend = lambda: Text.from_markup(str(lab.border_subtitle)).plain  # noqa: E731
            assert "^N switch focus: advice <-> agent list" in legend()
            assert "rename all" not in legend()
            assert "FLEET live  archived  5 · ^N to switch here" in lab.render_rows().plain
            assert "^N switches focus to the agent list: Enter renames one, A all" in \
                lab.render_rows().plain
            await pilot.press("ctrl+n")              # the rows: A is a key, not text
            await pilot.pause()
            assert "Enter rename · A rename all · ^N switch focus: advice <-> agent list" in legend()
            assert "· focused" in lab.render_rows().plain
            await pilot.press("A")
            assert "y rename default/auto names" in str(lab.border_subtitle).replace("  ", " ")
            assert "naming today" not in lab.render_rows().plain   # the question's whole line
            tip = lab.render_rows().plain
            assert "rename 2 agents (default/auto names) to their picks? y: yes" in tip
            assert "a: all 3, with 1 people named" in tip
            await pilot.press("escape")              # cancel: nothing renamed
            assert lab._confirm_all is None and renames == []
            await pilot.press("A")
            await pilot.press("a")                   # every name: asks again
            assert "rename ALL" in str(lab.border_subtitle)
            assert "rename ALL 3 agents, with 1 named by people" in lab.render_rows().plain
            assert renames == []
            await pilot.press("a")
            await _until(pilot, lambda: len(renames) == 3)
            assert sorted(renames) == [("kid", "billing-codes-dialog-in-the-tui", "human"),
                                       ("proj", "wiki", "human"), ("proj2", "docs", "human")]

    async def test_a_then_y_leaves_names_people_chose(self, make_tui, stub, monkeypatch):
        model = StubModel({**ANSWERS, "docs site": {"candidates": ["docs"], "keep": False}})
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)
        self._seed()
        renames = []

        def rename(self, session, new_name, force=False, source="human"):
            renames.append((session.name, new_name, source))
            self.sessions.update_session(session.id, name=new_name, name_source=source)
            return True
        monkeypatch.setattr("overcode.launcher.AgentLauncher.rename", rename)
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            await _until(pilot, lambda: lab._runner.calls >= 4)
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+n", "A", "y")
            await _until(pilot, lambda: len(renames) == 2)
            await pilot.pause()
            assert sorted(renames) == [("proj", "wiki", "human"), ("proj2", "docs", "human")]

    async def test_ctrl_t_archived_ctrl_d_pins_esc_twice(self, make_tui, stub):
        fleet = self._seed()
        sm = SessionManager(skip_git_detection=True)
        sm.delete_session(fleet[2].id)
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            await pilot.press("ctrl+d")
            assert lab.pinned == lab.current()
            await pilot.press("ctrl+t")
            await _until(pilot, lambda: lab.view == "archived" and not lab.loading)
            assert [a.name for a in lab.agents] == ["proj2"]
            await pilot.press("x")
            await _until(pilot, lambda: lab.dirty)
            await pilot.press("escape")
            assert lab.has_class("visible")
            await pilot.press("escape")
            await pilot.pause()
            assert not lab.has_class("visible") and app.is_running

    async def test_no_model_no_calls_but_editing_works(self, make_tui, stub):
        stub.available = False
        self._seed()
        app = make_tui()
        async with app.run_test(size=(160, 45)) as pilot:
            lab = await self._open(pilot)
            lab._tick()
            await app.workers.wait_for_complete()
            assert stub.prompts == [] and "no model" in lab.render_rows().plain
            await pilot.press("ctrl+o")
            assert lab.kind == "prompt" and lab.draft == sp.DEFAULT_NAMING_PROMPT

    async def test_listed_in_the_palette_and_help(self):
        from overcode.command_palette import COMMANDS
        from overcode.keymap import SCOPES, default_scope_bindings
        entry = next(c for c in COMMANDS if c.action == "open_naming_lab")
        assert entry.title == "Naming lab…"
        assert "naming_lab" in SCOPES
        actions = {b.action for b in default_scope_bindings()["naming_lab"]}
        assert {"save", "revert", "load_default", "switch_view", "pin", "apply"} <= actions


# ── overcode name lab ────────────────────────────────────────────────────

class TestNameLabCLI:
    @pytest.fixture
    def run(self, home, monkeypatch):
        from typer.testing import CliRunner
        from overcode.cli import app
        monkeypatch.setattr("overcode.cli.split.open_split", lambda *a, **k: None)
        model = StubModel(ANSWERS)
        monkeypatch.setattr("overcode.namer.make_client", lambda provider=None: model)

        def run(*args):
            return CliRunner().invoke(app, ["name", "lab", *args])
        run.model = model
        return run

    def test_json_rows_a_and_b_and_csv(self, run, tmp_path):
        sm = SessionManager(skip_git_detection=True)
        _fleet(sm)
        a, b = tmp_path / "a.md", tmp_path / "b.md"
        a.write_text("ADVICE-A")
        b.write_text("ADVICE-B")
        out_csv = tmp_path / "out.csv"
        result = run("--advice-file", str(a), "--vs", str(b), "--csv", str(out_csv), "--json")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip().splitlines()[-1])
        rows = {r["agent"]: r for r in data["rows"]}
        assert rows["proj"]["pick_a"] == "wiki" and rows["proj"]["collides_a"]
        assert rows["auth"]["locked"] and rows["quiet"]["error_a"] == "no recorded brief or prompts"
        assert data["calls"] == 8 and data["summary"]["a"]["collisions"] == 2
        assert sum("ADVICE-A" in p for p in run.model.prompts) == 4
        assert sum("ADVICE-B" in p for p in run.model.prompts) == 4
        lines = out_csv.read_text().splitlines()
        assert lines[0] == "agent,name_source,pick_a,pick_b,rejected" and len(lines) == 6
        # Nothing renamed; each call a ledger row under overcode
        assert [s.name for s in SessionManager(skip_git_detection=True).list_sessions()] == [
            "auth", "proj", "proj2", "kid", "quiet"]
        assert len([r for r in sm.ledger.iter_rows() if r.get("namer")]) == 8

    def test_archived_table(self, run):
        sm = SessionManager(skip_git_detection=True)
        fleet = _fleet(sm)
        for s in fleet[1:3]:
            sm.delete_session(s.id)
        import re
        result = run("--archived", "50")
        assert result.exit_code == 0, result.output
        out = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "proj2" in out and "wiki" in out and "collides" in out
        assert "auth" not in out
        assert "A: 2 changed, 2 collisions" in out

    def test_no_model(self, run):
        run.model.available = False
        result = run()
        assert result.exit_code == 1 and "no model" in result.output
