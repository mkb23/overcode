"""The billing codes dialog: spend per code from the ledger, renames and
merges in place (``overcode billing rename`` / ``merge``'s paths), and the
agents' name copies refreshed at once. Synthetic ledgers only."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from overcode.ledger import Ledger
from overcode.session_manager import SessionManager
from overcode.tui_widgets.billing_codes_modal import (
    BillingCodesModal,
    CodeRow,
    load_code_rows,
    month_starts,
)


def _press(modal, key, character=None):
    modal.on_key(SimpleNamespace(key=key, character=character, stop=lambda: None))


def _type(modal, text):
    for ch in text:
        _press(modal, ch, ch)


def _row(sid, code, name, cost, t, kind="checkpoint", seq=1):
    return {"t": t.isoformat(timespec="seconds"), "seq": seq, "sid": sid, "name": name,
            "code": code, "billing_code": "?", "seg": 0, "cost": cost,
            "tokens": int(cost * 100), "tok_in": 0, "tok_out": 0, "tok_cache": 0,
            "base_cost": 0.0, "base_tokens": 0, "kind": kind}


def _seed(ledger):
    """api: one agent, $5 by last month, $8 now (closed). web: two agents this month, one running."""
    this_start, last_start = month_starts(datetime.now())
    last, this = last_start + timedelta(days=3), this_start + timedelta(minutes=5)
    api = ledger.codes.resolve("api")
    web = ledger.codes.resolve("web")
    ledger.codes.resolve("idle")
    ledger.append([
        _row("a1", api, "api-agent", 5.0, last),
        _row("a1", api, "api-agent", 8.0, this, kind="final", seq=2),
        _row("w1", web, "web-one", 12.0, this, kind="final"),
        _row("w2", web, "web-two", 9.5, this),
    ])
    return api, web


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state")


class TestLoadCodeRows:
    def test_rows_by_total_with_the_months_agents_and_source(self, ledger):
        api, web = _seed(ledger)
        rows = load_code_rows(ledger)
        assert [r.name for r in rows] == ["web", "api", "idle"]
        w, a, idle = rows
        assert (w.id, w.total, w.this_month, w.last_month) == (web, 21.5, 21.5, 0.0)
        assert (w.agents, w.running, w.source) == (2, 1, "default")
        assert w.members == [("web-one", 12.0, False), ("web-two", 9.5, True)]
        assert (a.total, a.this_month, a.last_month) == (8.0, 3.0, 5.0)
        assert (idle.total, idle.agents, idle.members) == (0.0, 0, [])

    def test_a_merged_code_is_folded_into_the_other(self, ledger):
        api, web = _seed(ledger)
        ledger.codes.merge(api, web)
        rows = load_code_rows(ledger)
        assert [r.name for r in rows] == ["web", "idle"]
        assert rows[0].total == 29.5 and rows[0].last_month == 5.0
        assert "api" in rows[0].previous_names
        assert {m[0] for m in rows[0].members} == {"api-agent", "web-one", "web-two"}

    def test_reads_the_ledger_once_for_every_period(self, ledger, monkeypatch):
        _seed(ledger)
        reads = []
        real = Ledger.iter_rows
        monkeypatch.setattr(Ledger, "iter_rows", lambda self: reads.append(1) or real(self))
        load_code_rows(ledger)
        assert len(reads) == 1


# ── the dialog ───────────────────────────────────────────────────────────

def _rows():
    return [
        CodeRow("c_" + "1" * 32, "web", "human", ["frontend"], 21.5, 21.5, 0.0, 2, 1,
                members=[("web-one", 12.0, False), ("web-two", 9.5, True)]),
        CodeRow("c_" + "2" * 32, "api", "default", [], 8.0, 3.0, 5.0, 1, 0),
        CodeRow("old-string", "old-string", registered=False, total=1.0),
    ]


def _modal(rows=None, inner=96):
    m = BillingCodesModal(id="bc")
    m.show()
    m._inner_width = inner
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    m.set_rows(_rows() if rows is None else rows)
    return m, posted


class TestBillingCodesModal:
    def test_waits_for_the_ledger_then_lists_codes_by_spend(self):
        m = BillingCodesModal(id="bc")
        m.show()
        assert "reading the ledger" in m.render().plain
        m.set_rows(_rows())
        text = m.render().plain
        assert text.index("web") < text.index("api") < text.index("old-string")
        assert "$21.50" in text and "this month" in text and "last month" in text
        assert "3 codes" in text and "$30.50" in text

    def test_the_focused_rows_previous_names_and_id(self):
        m, _ = _modal()
        assert "previously: frontend" in m.render().plain
        assert "c_" + "1" * 32 in m.render().plain
        _press(m, "j")
        assert "no previous names" in m.render().plain

    def test_enter_renames_in_place(self):
        m, posted = _modal()
        _press(m, "j")
        _press(m, "enter")
        assert m.mode == "edit" and m.value == "api"
        for _ in range(3):
            _press(m, "backspace")
        _type(m, "billing-api")
        assert "renames api to billing-api" in m.render().plain
        _press(m, "enter")
        msg = posted[-1]
        assert isinstance(msg, BillingCodesModal.RenameRequested)
        assert (msg.code_id, msg.old, msg.new) == ("c_" + "2" * 32, "api", "billing-api")
        assert m.mode == "list"

    def test_r_also_renames_and_esc_cancels(self):
        m, posted = _modal()
        _press(m, "r")
        _type(m, "x")
        _press(m, "escape")
        assert m.mode == "list" and posted == []
        _press(m, "escape")
        assert [type(p) for p in posted] == [BillingCodesModal.Closed]

    @pytest.mark.parametrize("typed, problem", [
        ("", "cannot be empty"), ("c_" + "a" * 32, "looks like a code id")])
    def test_refuses_what_the_registry_would(self, typed, problem):
        m, posted = _modal()
        _press(m, "enter")
        m.value = typed
        assert problem in m.render().plain
        _press(m, "enter")
        assert posted == [] and m.mode == "edit"

    def test_an_unregistered_legacy_code_cannot_be_renamed(self):
        m, posted = _modal()
        _press(m, "k")  # wraps to the last row
        _press(m, "enter")
        _type(m, "x")
        assert "not in the code registry" in m.render().plain
        _press(m, "enter")
        assert posted == []

    def test_unchanged_just_closes_the_edit(self):
        m, posted = _modal()
        _press(m, "enter")
        assert "unchanged" in m.render().plain
        _press(m, "enter")
        assert posted == [] and m.mode == "list"

    def test_another_codes_name_offers_a_merge_on_a_second_enter(self):
        m, posted = _modal()
        _press(m, "j")
        _press(m, "enter")
        m.value, m.cursor = "web", 3
        assert "merges api into it" in m.render().plain
        _press(m, "enter")
        assert posted == []
        assert "merge api into web? press Enter again" in m.render().plain
        _press(m, "enter")
        msg = posted[-1]
        assert isinstance(msg, BillingCodesModal.MergeRequested)
        assert (msg.code_id, msg.into_id) == ("c_" + "2" * 32, "c_" + "1" * 32)

    def test_an_edit_after_the_offer_needs_the_confirm_again(self):
        m, posted = _modal()
        _press(m, "j")
        _press(m, "enter")
        m.value, m.cursor = "we", 2
        _type(m, "b")
        _press(m, "enter")
        _press(m, "backspace")
        _type(m, "b")
        _press(m, "enter")
        assert posted == []

    def test_another_codes_previous_name_is_allowed_with_a_warning(self):
        m, posted = _modal()
        _press(m, "j")
        _press(m, "enter")
        m.value, m.cursor = "frontend", 8
        assert "was a name of web" in m.render().plain
        _press(m, "enter")
        assert isinstance(posted[-1], BillingCodesModal.RenameRequested)

    def test_renamed_updates_the_row_without_a_ledger_read(self):
        m, _ = _modal()
        m.renamed("c_" + "2" * 32, "billing-api", ["api"])
        _press(m, "j")
        text = m.render().plain
        assert "billing-api" in text and "previously: api" in text

    def test_the_highlight_follows_its_code_when_rows_reload(self):
        m, _ = _modal()
        _press(m, "j")
        rows = _rows()
        m.set_rows([rows[1], rows[0], rows[2]])
        assert m.current.name == "api"

    def test_filter_as_you_type(self):
        m, _ = _modal()
        _press(m, "slash")
        _type(m, "front")  # a previous name matches too
        assert [r.name for r in m.shown] == ["web"]
        _press(m, "enter")
        assert m.mode == "list" and "filter" in m.render().plain
        _press(m, "escape")
        assert len(m.shown) == 3 and m.mode == "list"

    def test_agents_drill_down(self):
        m, posted = _modal()
        _press(m, "a")
        text = m.render().plain
        assert "web-one" in text and "$12.00" in text and "running" in text
        _press(m, "escape")
        assert m.mode == "list" and posted == []
        _press(m, "right")
        assert m.mode == "agents"
        _press(m, "left")
        assert m.mode == "list"

    @pytest.mark.parametrize("cols", [120, 160, 200, 260])
    @pytest.mark.parametrize("mode", ["list", "edit", "filter", "agents"])
    def test_every_line_is_the_dialogs_width(self, cols, mode):
        long = CodeRow("c_" + "3" * 32, "a-very-long-billing-code-name-" * 3, total=123456.78,
                       this_month=99999.99, last_month=1234.5, agents=1234, running=56,
                       members=[("an-agent-with-a-long-name-" * 4, 99999.0, True)])
        m, _ = _modal(_rows() + [long], inner=min(BillingCodesModal.WIDTH, cols - 2) - 4)
        m.selected_index = 3
        {"edit": lambda: _press(m, "enter"), "filter": lambda: _press(m, "slash"),
         "agents": lambda: _press(m, "a"), "list": lambda: None}[mode]()
        lines = m.render().plain.split("\n")
        assert all(cell_len(line) == m.inner_width for line in lines), lines


# ── the app ──────────────────────────────────────────────────────────────

def test_the_palette_lists_it_for_help():
    from overcode.command_palette import COMMANDS
    from overcode.tui_widgets.help_overlay import build_help

    entry = next(c for c in COMMANDS if c.action == "open_billing_codes")
    assert entry.title == "Billing codes…" and not entry.agent
    assert "Billing codes…" in build_help(200).plain


@pytest.mark.asyncio
class TestFromTheTui:
    async def _open(self, app, pilot):
        from tests.tui_harness import ready

        await ready(pilot)
        app.action_open_billing_codes()
        await app.workers.wait_for_complete()
        await pilot.pause()
        modal = app.query_one("#billing-codes-modal", BillingCodesModal)
        assert modal.has_class("visible") and modal.rows is not None
        return modal

    async def test_rename_reaches_the_registry_and_the_agents_name_copies(self, make_tui):
        sm = SessionManager(skip_git_detection=True)
        s = sm.create_session(name="alpha", tmux_session="test", tmux_window="alpha-w",
                              command=["claude"], start_directory="/tmp")
        code_id = sm.get_session(s.id).billing_code_id
        sm.ledger.append([_row(s.id, code_id, "alpha", 4.0, datetime.now())])
        app = make_tui()
        async with app.run_test(size=(160, 40)) as pilot:
            modal = await self._open(app, pilot)
            assert [r.name for r in modal.rows] == ["alpha"]
            assert modal.rows[0].total == 4.0
            await pilot.press("enter")
            for _ in range(5):
                await pilot.press("backspace")
            await pilot.press(*"client-q", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert modal.rows[0].name == "client-q"
            assert "alpha is now client-q" in modal.render().plain
            await pilot.press("j")  # moving clears the message: the row's previous names
            assert "previously: alpha" in modal.render().plain
            await pilot.press("q")  # closes the dialog, not the app
            await pilot.pause()
            assert not modal.has_class("visible") and app.is_running

        codes = sm.ledger.codes
        assert codes.name_of(code_id) == "client-q"
        assert SessionManager(skip_git_detection=True).get_session(s.id).billing_code == "client-q"

    async def test_merge_with_a_double_enter_and_the_figures_read_again(self, make_tui):
        sm = SessionManager(skip_git_detection=True)
        a = sm.create_session(name="alpha", tmux_session="test", tmux_window="a-w",
                              command=["claude"], start_directory="/tmp")
        b = sm.create_session(name="bravo", tmux_session="test", tmux_window="b-w",
                              command=["claude"], start_directory="/tmp")
        ida, idb = (sm.get_session(x.id).billing_code_id for x in (a, b))
        now = datetime.now()
        sm.ledger.append([_row(a.id, ida, "alpha", 2.0, now), _row(b.id, idb, "bravo", 7.0, now)])
        app = make_tui()
        async with app.run_test(size=(160, 40)) as pilot:
            modal = await self._open(app, pilot)
            assert [r.name for r in modal.rows] == ["bravo", "alpha"]
            await pilot.press("j", "enter")
            for _ in range(5):
                await pilot.press("backspace")
            await pilot.press(*"bravo", "enter")
            assert "press Enter again" in modal.render().plain
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [(r.name, r.total) for r in modal.rows] == [("bravo", 9.0)]
            assert "folded into bravo" in modal.render().plain

        fresh = SessionManager(skip_git_detection=True)
        assert fresh.get_session(a.id).billing_code_id == idb
        assert fresh.get_session(a.id).billing_code == "bravo"
