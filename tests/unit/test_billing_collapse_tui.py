"""Collapsing codes into the default from the billing codes dialog (m), and the
new-agent billing settings (New agent defaults; s in the dialog). Synthetic
ledgers and configs only."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from overcode.config import get_billing_defaults
from overcode.session_manager import SessionManager
from overcode.tui_widgets.billing_codes_modal import BillingCodesModal, CodeRow
from overcode.tui_widgets.new_agent_defaults_modal import BILLING_MODES, NewAgentDefaultsModal
from overcode.tui_widgets.new_agent_modal import NewAgentModal

NOW = datetime.now()
WEB, API, TINY, GEN, OLD = ("c_" + c * 32 for c in "12345")


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    monkeypatch.setattr("overcode.config.CONFIG_PATH", tmp_path / "config.yaml")


def _press(modal, key, character=None):
    modal.on_key(SimpleNamespace(key=key, character=character, stop=lambda: None))


def _type(modal, text):
    for ch in text:
        _press(modal, ch, ch)


def _rows():
    return [
        CodeRow(WEB, "web", "human", [], 21.5, 21.5, 0.0, 2, 1, last=NOW),
        CodeRow(API, "api", "default", [], 8.0, 3.0, 5.0, 1, 0, last=NOW - timedelta(days=40)),
        CodeRow(TINY, "tiny", "default", [], 0.4, 0.0, 0.4, 1, 0, last=NOW - timedelta(days=50)),
        CodeRow(GEN, "general", "human", ["scratch"], 0.2, 0.2, 0.0, 1, 0, last=NOW),
        CodeRow(OLD, "scratch", "default", [], 0.0, 0.0, 0.0, 0, 0, merged_into="general"),
    ]


def _modal(inner=96, settings=None):
    m = BillingCodesModal(id="bc")
    m.show()
    m._inner_width = inner
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    m.set_rows(_rows(), settings=settings or {"new_agent_code": "name",
                                              "default_code": "general", "warnings": []},
               default_id=GEN)
    return m, posted


def _at(m, name):
    """Highlight the code called ``name``, as moving there does."""
    m.selected_index = [r.name for r in m.shown].index(name)
    m._chosen = m.current.id
    m._message = None


class TestCollapseKeys:
    def test_merged_codes_are_hidden_until_m(self):
        m, _ = _modal()
        assert "scratch" not in [r.name for r in m.shown]
        assert "4 codes" in m.render().plain
        _press(m, "m", "m")
        assert "scratch" in [r.name for r in m.shown]
        assert "4 codes" in m.render().plain  # a merged row is not a code of its own

    def test_c_twice_collapses_the_highlighted_code(self):
        m, posted = _modal()
        _at(m, "api")
        _press(m, "c", "c")
        text = m.render().plain
        assert "collapse api ($8.00) into general? press c again" in text
        assert not posted
        _press(m, "c", "c")
        assert isinstance(posted[0], BillingCodesModal.CollapseRequested)
        assert posted[0].code_ids == [API] and posted[0].names == ["api"]

    def test_any_other_key_drops_the_confirm(self):
        m, posted = _modal()
        _at(m, "api")
        _press(m, "c", "c")
        _press(m, "j", "j")
        _press(m, "c", "c")
        assert not posted and "press c again" in m.render().plain

    def test_the_default_code_cannot_be_marked_or_collapsed(self):
        m, posted = _modal()
        _at(m, "general")
        assert "general ★" in m.render().plain
        _press(m, "space", " ")
        assert not m.marks
        _press(m, "c", "c")
        _press(m, "c", "c")
        assert not posted and "cannot be collapsed" in m.render().plain

    def test_marks_collapse_together_with_one_confirm(self):
        m, posted = _modal()
        _at(m, "api")
        _press(m, "space", " ")
        _at(m, "tiny")
        _press(m, "space", " ")
        assert m.marks == {API, TINY}
        text = m.render().plain
        assert "✓ api" in text and "2 marked" in text
        _press(m, "c", "c")
        assert "collapse 2 codes ($8.40) into general?" in m.render().plain
        _press(m, "c", "c")
        assert set(posted[0].code_ids) == {API, TINY}
        assert not m.marks

    def test_space_again_unmarks_and_esc_clears_marks_before_closing(self):
        m, posted = _modal()
        _at(m, "api")
        _press(m, "space", " ")
        _press(m, "space", " ")
        assert not m.marks
        _press(m, "space", " ")
        _press(m, "escape")
        assert not m.marks and not posted
        _press(m, "escape")
        assert isinstance(posted[-1], BillingCodesModal.Closed)

    def test_mark_under_an_amount(self):
        m, _ = _modal()
        _press(m, "dollar_sign", "$")
        assert m.mode == "prompt" and "mark codes under $" in m.render().plain
        _type(m, "1")
        _press(m, "enter")
        assert m.marks == {TINY}  # general is the default; scratch is merged
        assert "marked 1 code under $1.00" in m.render().plain

    def test_mark_idle_days_skips_running_and_recent_codes(self):
        m, _ = _modal()
        _press(m, "i", "i")
        _type(m, "30")
        _press(m, "enter")
        assert m.marks == {API, TINY}
        _press(m, "escape")
        _press(m, "i", "i")
        _type(m, "45")
        _press(m, "enter")
        assert m.marks == {TINY}

    def test_a_bad_amount_says_so_and_esc_cancels_the_prompt(self):
        m, _ = _modal()
        _press(m, "dollar_sign", "$")
        _press(m, "escape")
        assert m.mode == "list" and not m.marks
        _press(m, "dollar_sign", "$")
        _type(m, "..")
        _press(m, "enter")
        assert "not a number" in m.render().plain

    def test_u_twice_unmerges_and_says_live_agents_stay(self):
        m, posted = _modal()
        _press(m, "m", "m")
        _at(m, "scratch")
        text = m.render().plain
        assert "merged into general · u unmerges" in text
        _press(m, "u", "u")
        assert "its live agents stay on general" in m.render().plain
        _press(m, "u", "u")
        assert isinstance(posted[0], BillingCodesModal.UnmergeRequested)
        assert (posted[0].code_id, posted[0].into) == (OLD, "general")

    def test_u_on_a_code_that_is_not_merged(self):
        m, posted = _modal()
        _press(m, "u", "u")
        _press(m, "u", "u")
        assert not posted and "m shows them" in m.render().plain

    def test_a_merged_code_cannot_be_renamed(self):
        m, _ = _modal()
        _press(m, "m", "m")
        _at(m, "scratch")
        _press(m, "enter")
        assert "is merged into general" in m.render().plain

    def test_s_asks_for_the_settings(self):
        m, posted = _modal()
        _press(m, "s", "s")
        assert isinstance(posted[0], BillingCodesModal.SettingsRequested)

    def test_the_settings_line(self):
        m, _ = _modal(settings={"new_agent_code": "folder", "default_code": "ops",
                                "warnings": ["billing.new_agent_code: 'x' is not one of"]})
        text = m.render().plain
        assert "new agents bill to their repo · default code ops" in text
        assert "config.yaml: billing.new_agent_code" in text

    @pytest.mark.parametrize("cols", [120, 160, 200, 260])
    @pytest.mark.parametrize("state", ["marks", "merged", "prompt", "confirm", "unmerge"])
    def test_every_line_is_the_dialogs_width(self, cols, state):
        m, _ = _modal(inner=min(BillingCodesModal.WIDTH, cols - 2) - 4)
        m.rows.append(CodeRow("c_" + "9" * 32, "a-very-long-billing-code-name-" * 3,
                              total=123456.78, this_month=99999.99, agents=1234, running=56))
        _at(m, "api")
        if state == "marks":
            _press(m, "space", " ")
        elif state == "merged":
            _press(m, "m", "m")
        elif state == "prompt":
            _press(m, "i", "i")
            _type(m, "3000")
        elif state == "confirm":
            _press(m, "c", "c")
        else:
            _press(m, "m", "m")
            _at(m, "scratch")
            _press(m, "u", "u")
        lines = m.render().plain.split("\n")
        assert all(cell_len(line) == m.inner_width for line in lines), lines


# ── New agent defaults ───────────────────────────────────────────────────


def _defaults_modal(billing=None, **kw):
    m = NewAgentDefaultsModal(id="nad")
    m.show({"backend": "claude-code", "backend_explicit": False},
           billing=billing or {"new_agent_code": "name", "default_code": "general"}, **kw)
    m._inner_width = 88
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    return m, posted


class TestDefaultsModal:
    def test_billing_rows_cycle_type_and_save(self):
        m, posted = _defaults_modal(at_billing=True)
        assert m.selected_index == m._billing_row()
        text = m.render().plain
        assert "Billing code" in text and "Default code" in text and "general" in text
        _press(m, "space", " ")
        assert m.billing_mode == "folder"
        _press(m, "down")
        _press(m, "enter")
        assert m.editing
        for _ in range(len("general")):
            _press(m, "backspace")
        _type(m, "ops q")  # q and space are typed, not commands, while editing
        _press(m, "enter")
        assert not m.editing and m.default_code == "ops q"
        _press(m, "a", "a")
        assert posted[0].billing == {"new_agent_code": "folder", "default_code": "ops q"}
        assert "billing" not in posted[0].defaults

    def test_esc_undoes_the_edit_and_empty_keeps_the_old_name(self):
        m, _ = _defaults_modal(at_billing=True)
        _press(m, "down")
        _press(m, "enter")
        _type(m, "xyz")
        _press(m, "escape")
        assert m.default_code == "general" and not m.editing
        _press(m, "enter")
        for _ in range(10):
            _press(m, "backspace")
        _press(m, "enter")
        assert m.default_code == "general"

    def test_modes_match_the_config(self):
        from overcode.config import NEW_AGENT_CODE_MODES
        assert BILLING_MODES == NEW_AGENT_CODE_MODES

    @pytest.mark.parametrize("cols", [120, 260])
    def test_every_line_is_the_dialogs_width(self, cols):
        m, _ = _defaults_modal()
        m._inner_width = min(NewAgentDefaultsModal.WIDTH, cols - 2) - 4
        for i in range(m._row_count()):
            m.selected_index = i
            lines = m.render().plain.split("\n")
            assert all(cell_len(line) == m.inner_width for line in lines), lines


# ── the new-agent dialog's Billing row ───────────────────────────────────


def _new_agent(tmp_path, mode, default_code="general"):
    repo = tmp_path / "proj"
    (repo / "sub").mkdir(parents=True)
    (repo / ".git").mkdir()
    m = NewAgentModal(id="na")
    m.show(directory=str(repo / "sub"), defaults={}, agents=[], existing_names=set(),
           billing_codes=["client-a"],
           billing_defaults={"new_agent_code": mode, "default_code": default_code})
    m._inner_width = 92
    m.selected_index = 3
    return m


class TestNewAgentBillingRow:
    @pytest.mark.parametrize("mode,shown", [("name", "(sub)"), ("folder", "(proj)"),
                                            ("default", "(ops)")])
    def test_the_default_follows_the_mode(self, tmp_path, mode, shown):
        m = _new_agent(tmp_path, mode, "ops")
        assert shown in m.render().plain
        assert m._launch_billing_code() is None

    def test_typing_the_default_code_is_the_default(self, tmp_path):
        m = _new_agent(tmp_path, "default", "ops")
        f = m._field("billing")
        f.value = m._billing_value(" ops ")
        assert f.value == "" and m._launch_billing_code() is None
        f.value = m._billing_value("sub")  # the agent's name is a code of its own here
        assert m._launch_billing_code() == "sub"

    def test_space_cycles_the_default_then_the_shortlist(self, tmp_path):
        m = _new_agent(tmp_path, "folder")
        _press(m, "space", " ")
        assert m._launch_billing_code() == "client-a"
        _press(m, "space", " ")
        assert m._launch_billing_code() is None


# ── the app ──────────────────────────────────────────────────────────────


def _ledger_row(sid, code, name, cost, t):
    return {"t": t.isoformat(timespec="seconds"), "seq": 1, "sid": sid, "name": name,
            "code": code, "billing_code": name, "seg": 0, "cost": cost,
            "tokens": int(cost * 100), "tok_in": 0, "tok_out": 0, "tok_cache": 0,
            "base_cost": 0.0, "base_tokens": 0, "kind": "checkpoint"}


@pytest.mark.asyncio
class TestFromTheTui:
    async def _open(self, app, pilot):
        from tests.tui_harness import ready

        await ready(pilot)
        app.action_open_billing_codes()
        await self._settle(app, pilot)
        modal = app.query_one("#billing-codes-modal", BillingCodesModal)
        assert modal.has_class("visible") and modal.rows is not None
        return modal

    async def _settle(self, app, pilot):
        for _ in range(2):
            await app.workers.wait_for_complete()
            await pilot.pause()

    async def test_collapse_then_unmerge(self, make_tui):
        sm = SessionManager(skip_git_detection=True)
        a = sm.create_session(name="alpha", tmux_session="test", tmux_window="a-w",
                              command=["claude"], start_directory="/tmp")
        b = sm.create_session(name="bravo", tmux_session="test", tmux_window="b-w",
                              command=["claude"], start_directory="/tmp")
        ida, idb = (sm.get_session(x.id).billing_code_id for x in (a, b))
        sm.ledger.append([_ledger_row(a.id, ida, "alpha", 2.0, NOW),
                          _ledger_row(b.id, idb, "bravo", 7.0, NOW)])
        app = make_tui()
        async with app.run_test(size=(160, 40)) as pilot:
            modal = await self._open(app, pilot)
            assert [r.name for r in modal.shown] == ["bravo", "alpha"]
            await pilot.press("space", "j", "space", "c", "c")
            await self._settle(app, pilot)
            assert [(r.name, r.total) for r in modal.shown] == [("general", 9.0)]
            assert modal.default_id is not None
            assert "2 codes collapsed into general" in modal.render().plain
            fresh = SessionManager(skip_git_detection=True)
            gen = fresh.ledger.codes.find("general")
            assert fresh.ledger.codes.get(gen)["source"] == "human"
            assert fresh.get_session(a.id).billing_code_id == gen
            assert fresh.get_session(a.id).billing_code == "general"

            await pilot.press("m")
            _at(modal, "alpha")
            await pilot.press("u", "u")
            await self._settle(app, pilot)
            assert ("alpha", 2.0) in [(r.name, r.total) for r in modal.shown]
            assert "agents stay on general" in modal.render().plain
        fresh = SessionManager(skip_git_detection=True)
        assert fresh.ledger.codes.canonical(ida) == ida
        assert fresh.get_session(a.id).billing_code_id == gen  # live agents stay

    async def test_s_edits_the_settings_and_comes_back(self, make_tui):
        app = make_tui()
        async with app.run_test(size=(160, 40)) as pilot:
            modal = await self._open(app, pilot)
            assert "new agents bill to their name" in modal.render().plain
            await pilot.press("s")
            await pilot.pause()
            defaults = app.query_one("#new-agent-defaults-modal", NewAgentDefaultsModal)
            assert defaults.has_class("visible") and not modal.has_class("visible")
            assert defaults.selected_index == defaults._billing_row()
            await pilot.press("space", "space", "a")  # name → folder → default
            await self._settle(app, pilot)
            assert get_billing_defaults()["new_agent_code"] == "default"
            assert modal.has_class("visible") and not defaults.has_class("visible")
            assert "new agents bill to general" in modal.render().plain
