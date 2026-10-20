"""Billing codes in the TUI: the new-agent modal's Billing row, the
billing-code dialog for an existing agent, and its rebill (forward only,
the same path as ``overcode rebill``). Synthetic records only."""

from types import SimpleNamespace

import pytest

from overcode.session_manager import SessionManager
from overcode.tui_widgets.billing_code_modal import (
    BillingCodeModal,
    billing_cycle,
    cycle_labels,
    next_in_cycle,
)
from overcode.tui_widgets.new_agent_modal import NewAgentModal


def _press(modal, key, character=None):
    modal.on_key(SimpleNamespace(key=key, character=character, stop=lambda: None))


def _new_agent_modal(tmp_path, codes=("clientA", "clientB"), parent_code=None):
    m = NewAgentModal(id="m")
    m.show(directory=str(tmp_path / "proj"), defaults={}, agents=[], existing_names=set(),
           local_hostname="here", billing_codes=list(codes), parent_billing_code=parent_code)
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    m.selected_index = [f.key for f in m.fields].index("billing")
    return m, posted


# ── the cycle ────────────────────────────────────────────────────────────

class TestCycle:
    def test_default_then_the_shortlist_then_back(self):
        codes = billing_cycle("alpha", ["clientA", "clientB"])
        assert codes == ["alpha", "clientA", "clientB"]
        seen, value = [], "alpha"
        for _ in range(4):
            value = next_in_cycle(codes, value)
            seen.append(value)
        assert seen == ["clientA", "clientB", "alpha", "clientA"]

    def test_the_name_is_not_listed_twice(self):
        assert billing_cycle("alpha", ["alpha", "x"]) == ["alpha", "x"]

    def test_a_current_code_off_the_list_leads_so_the_cycle_comes_back_to_it(self):
        codes = billing_cycle("alpha", ["clientA"], current="legacy")
        assert codes == ["legacy", "alpha", "clientA"]
        assert next_in_cycle(codes, "clientA") == "legacy"

    def test_a_typed_code_cycles_to_the_start(self):
        assert next_in_cycle(["alpha", "x"], "typed") == "alpha"

    def test_the_default_is_drawn_in_brackets(self):
        assert cycle_labels(["alpha", "x"], "alpha") == ["(alpha)", "x"]


# ── new-agent modal ──────────────────────────────────────────────────────

class TestNewAgentBillingRow:
    def test_space_cycles_name_then_shortlist_then_back(self, tmp_path):
        m, _ = _new_agent_modal(tmp_path)
        f = m._field("billing")
        seen = [f.value]
        for _ in range(3):
            _press(m, "space")
            seen.append(f.value)
        assert seen == ["", "clientA", "clientB", ""]  # "" is the name: "(proj)"
        assert "(proj)" in m.render().plain

    def test_launches_with_the_chosen_code(self, tmp_path):
        m, posted = _new_agent_modal(tmp_path)
        _press(m, "space")
        m._launch()
        assert posted[-1].billing_code == "clientA"

    def test_the_default_launches_with_none(self, tmp_path):
        m, posted = _new_agent_modal(tmp_path)
        m._launch()
        assert posted[-1].billing_code is None  # the launcher bills it to its name

    def test_enter_types_a_custom_code(self, tmp_path):
        m, posted = _new_agent_modal(tmp_path)
        _press(m, "enter")
        for ch in "acme-42":
            _press(m, ch, ch)
        _press(m, "enter")
        assert m._field("billing").value == "acme-42"
        m._launch()
        assert posted[-1].billing_code == "acme-42"

    def test_typing_the_name_is_the_default(self, tmp_path):
        m, posted = _new_agent_modal(tmp_path)
        m.selected_index = [f.key for f in m.fields].index("billing")
        _press(m, "enter")
        for ch in "proj":
            _press(m, ch, ch)
        _press(m, "enter")
        assert m._field("billing").value == ""

    def test_a_child_shows_its_parents_code_and_it_cannot_change(self, tmp_path):
        m, posted = _new_agent_modal(tmp_path, parent_code="client-p")
        f = m._field("billing")
        assert (f.value, f.locked) == ("client-p", True)
        _press(m, "space")
        _press(m, "enter")
        assert f.value == "client-p" and not m._editing
        assert "inherited" in m.render().plain
        m._launch()
        assert posted[-1].billing_code is None  # the launcher inherits; it always wins

    def test_no_shortlist_cycles_only_the_name(self, tmp_path):
        m, _ = _new_agent_modal(tmp_path, codes=())
        _press(m, "space")
        assert m._field("billing").value == ""


# ── the billing-code dialog ──────────────────────────────────────────────

def _billing_modal(code="alpha", shortlist=("clientA", "clientB")):
    m = BillingCodeModal(id="b")
    m.show(session_id="sid-1", name="alpha", code=code, shortlist=list(shortlist))
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    return m, posted


class TestBillingCodeModal:
    def test_opens_on_the_current_code_and_space_cycles(self):
        m, _ = _billing_modal()
        assert m.value == "alpha"
        seen = []
        for _ in range(3):
            _press(m, "space")
            seen.append(m.value)
        assert seen == ["clientA", "clientB", "alpha"]

    def test_enter_applies_a_new_code(self):
        m, posted = _billing_modal()
        _press(m, "space")
        _press(m, "enter")
        msg = posted[-1]
        assert isinstance(msg, BillingCodeModal.RebillRequested)
        assert (msg.session_id, msg.old_code, msg.new_code) == ("sid-1", "alpha", "clientA")

    def test_typing_a_custom_code(self):
        m, posted = _billing_modal()
        for _ in range(5):
            _press(m, "backspace")
        for ch in "acme":
            _press(m, ch, ch)
        _press(m, "enter")
        assert posted[-1].new_code == "acme"

    @pytest.mark.parametrize("value, problem", [("", "enter a code"), ("alpha", "current code")])
    def test_enter_refuses_an_unusable_code(self, value, problem):
        m, posted = _billing_modal()
        m.value = value
        assert problem in m.error()
        _press(m, "enter")
        assert posted == []

    def test_esc_cancels(self):
        m, posted = _billing_modal()
        _press(m, "escape")
        assert [type(p) for p in posted] == [BillingCodeModal.Cancelled]

    def test_says_forward_only_and_that_children_keep_their_codes(self):
        m, _ = _billing_modal()
        _press(m, "space")
        text = m.render().plain
        assert "forward only" in text and "spend so far stays on alpha" in text
        assert "children already running keep their codes" in text

    def test_a_config_warning_is_shown(self):
        m = BillingCodeModal(id="b")
        m.show(session_id="s", name="alpha", code="alpha", config_warning="config.yaml: bad")
        m.value = "x"
        assert "config.yaml: bad" in m.render().plain


# ── the app: palette entry, the action, the rebill ───────────────────────

def test_the_palette_lists_it_for_help():
    from overcode.command_palette import COMMANDS

    entry = next(c for c in COMMANDS if c.action == "set_billing_code")
    assert entry.title == "Billing code…" and entry.agent


@pytest.mark.asyncio
class TestRebillFromTheTui:
    async def test_applying_goes_through_the_forward_only_rebill(self, make_tui):
        sm = SessionManager(skip_git_detection=True)
        s = sm.create_session(name="alpha", tmux_session="test", tmux_window="alpha-w",
                              command=["claude"], start_directory="/tmp")
        sm.update_stats(s.id, estimated_cost_usd=3.0, total_tokens=300)
        child = sm.create_session(name="kid", tmux_session="test", tmux_window="kid-w",
                                  command=["claude"], start_directory="/tmp",
                                  billing_code="alpha")
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause()
            app.focused_session_index = 0
            app._get_widgets_in_session_order()[0].focus()
            await pilot.pause()
            app.action_set_billing_code()
            await pilot.pause()
            modal = app.query_one("#billing-code-modal", BillingCodeModal)
            assert modal.has_class("visible") and modal.value == "alpha"
            for _ in range(5):
                await pilot.press("backspace")
            await pilot.press(*"client-y")
            await pilot.press("enter")
            await pilot.pause()

        assert sm.get_session(s.id).billing_code == "client-y"
        assert sm.get_session(child.id).billing_code == "alpha"  # per agent: children keep theirs
        rows = [(r["kind"], r["billing_code"]) for r in sm.ledger.iter_rows()]
        assert rows == [("close", "alpha"), ("open", "client-y")]
        assert sm.ledger.named(sm.ledger.totals())["alpha"]["cost"] == 3.0

    async def test_a_remote_agent_is_refused_with_how_to_do_it(self, make_tui, monkeypatch):
        from overcode.tui_actions import session as session_actions

        remote = SimpleNamespace(id="remote:h:a", name="a", is_remote=True, source_host="h")
        monkeypatch.setattr(session_actions, "_get_focused_session",
                            lambda tui: SimpleNamespace(session=remote))
        app = make_tui(sessions=[])
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause()
            notes = []
            app.notify = lambda msg, **kw: notes.append(msg)
            app.action_set_billing_code()
            assert not app.query_one("#billing-code-modal").has_class("visible")
            assert any("overcode rebill a" in n for n in notes)
