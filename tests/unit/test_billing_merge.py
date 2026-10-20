"""Merging billing codes as its own action, in bulk: ``codes.merge_many``,
``overcode billing merge CODE... --into`` and the dialog's ``M`` picker and
``*`` marks. Synthetic records only."""

import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from rich.cells import cell_len
from typer.testing import CliRunner

from overcode.cli import app
from overcode.codes import CodeError, CodeRegistry
from overcode.session_manager import SessionManager
from overcode.tui_widgets.billing_codes_modal import BillingCodesModal, CodeRow

runner = CliRunner()
NOW = datetime.now()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("overcode.config.CONFIG_PATH", tmp_path / "config.yaml")


def _row(code, cost, sid, name="x"):
    return {"t": NOW.isoformat(timespec="seconds"), "seq": 1, "sid": sid, "name": name,
            "code": code, "billing_code": name, "seg": 0, "cost": cost, "tokens": 1,
            "tok_in": 0, "tok_out": 0, "tok_cache": 0, "base_cost": 0.0, "base_tokens": 0,
            "kind": "final"}


# ── registry ─────────────────────────────────────────────────────────────


class TestMergeMany:
    def test_one_write_for_the_batch(self, tmp_path, monkeypatch):
        codes = CodeRegistry(tmp_path / "ledger")
        ids = [codes.resolve(n) for n in ("shirka", "shirka2", "shirka3", "fork1")]
        saves = []
        real = codes._save
        monkeypatch.setattr(codes, "_save", lambda data: (saves.append(1), real(data)))
        target, folded = codes.merge_many(["shirka2", "shirka3", ids[3], "shirka3"], "shirka")
        assert len(saves) == 1
        assert target == ids[0]
        assert [n for _, n in folded] == ["shirka2", "shirka3", "fork1"]
        assert all(codes.canonical(i) == ids[0] for i in ids)
        assert set(codes.get(ids[0])["previous_names"]) == {"shirka2", "shirka3", "fork1"}
        codes.unmerge("shirka3")
        assert codes.canonical(ids[2]) == ids[2]

    def test_refuses_the_target_and_unknown_codes_merging_nothing(self, tmp_path):
        codes = CodeRegistry(tmp_path / "ledger")
        a, b = codes.resolve("a"), codes.resolve("b")
        with pytest.raises(CodeError, match="same billing code"):
            codes.merge_many(["a", "b"], "b")
        with pytest.raises(CodeError, match="no billing code 'nope'"):
            codes.merge_many(["a", "nope"], "b")
        with pytest.raises(CodeError, match="no billing code 'new'"):
            codes.merge_many(["a"], "new")  # merge never makes the target
        assert codes.canonical(a) == a and codes.find("new") is None
        assert codes.merge("a", "b") == (a, b)

    def test_rename_onto_a_taken_name_points_at_merge(self, tmp_path):
        codes = CodeRegistry(tmp_path / "ledger")
        codes.resolve("a"), codes.resolve("b")
        with pytest.raises(CodeError, match="taken .* --into b"):
            codes.rename("a", "b")


# ── CLI ──────────────────────────────────────────────────────────────────


class TestMergeCli:
    def _fleet(self):
        sm = SessionManager(skip_git_detection=True)
        codes = sm.ledger.codes
        ids = {n: codes.resolve(n) for n in ("shirka", "shirka2", "shirka3", "shirka-fork1",
                                             "other")}
        sm.ledger.append([_row(ids["shirka2"], 2.0, "s2"), _row(ids["shirka3"], 3.0, "s3"),
                          _row(ids["shirka-fork1"], 4.0, "f1"), _row(ids["other"], 9.0, "o")])
        return sm, ids

    def test_old_two_positional_form(self):
        sm, ids = self._fleet()
        result = runner.invoke(app, ["billing", "merge", "shirka2", "shirka"])
        assert result.exit_code == 0, result.output
        assert "Billing code shirka2 folded into shirka" in result.output
        assert sm.ledger.codes.canonical(ids["shirka2"]) == ids["shirka"]

    def test_several_with_into(self):
        sm, ids = self._fleet()
        result = runner.invoke(app, ["billing", "merge", "shirka2", "shirka3", "--into", "shirka"])
        assert result.exit_code == 0, result.output
        assert "Merging 2 codes ($5.00, 2 agents) into shirka" in result.output
        assert "2 billing codes folded into shirka" in result.output
        codes = sm.ledger.codes
        assert codes.canonical(ids["shirka3"]) == ids["shirka"]
        assert codes.canonical(ids["shirka-fork1"]) == ids["shirka-fork1"]

    def test_glob_dry_run_lists_and_changes_nothing(self):
        sm, ids = self._fleet()
        result = runner.invoke(app, ["billing", "merge", "shirka*", "--into", "shirka",
                                     "--dry-run"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "Would merge 3 codes ($9.00, 3 agents) into shirka" in out
        assert "shirka-fork1" in out and "$     4.00" in out and "other" not in out
        assert sm.ledger.codes.canonical(ids["shirka2"]) == ids["shirka2"]

    def test_glob_asks_first_and_skips_the_target_and_merged_codes(self):
        sm, ids = self._fleet()
        sm.ledger.codes.merge("shirka3", "other")
        result = runner.invoke(app, ["billing", "merge", "shirka*", "--into", "shirka"],
                               input="n\n")
        assert result.exit_code == 1
        assert sm.ledger.codes.canonical(ids["shirka2"]) == ids["shirka2"]
        result = runner.invoke(app, ["billing", "merge", "shirka*", "--into", "shirka", "-y"])
        assert result.exit_code == 0, result.output
        assert "Merging 2 codes" in result.output
        codes = sm.ledger.codes
        assert codes.canonical(ids["shirka-fork1"]) == ids["shirka"]
        assert codes.canonical(ids["shirka3"]) == ids["other"]  # merged already: left alone
        assert sm.ledger.named(sm.ledger.totals())["shirka"]["cost"] == pytest.approx(6.0)

    def test_refusals(self):
        sm, ids = self._fleet()
        merge = ["billing", "merge"]
        assert runner.invoke(app, [*merge, "a", "b", "c"]).exit_code == 1    # needs --into
        assert runner.invoke(app, [*merge, "shirka2", "--into", "nope"]).exit_code == 1
        assert runner.invoke(app, [*merge, "nope", "--into", "shirka"]).exit_code == 1
        assert runner.invoke(app, [*merge, "zz*", "--into", "shirka"]).exit_code == 1
        assert runner.invoke(app, [*merge, "shirka", "--into", "shirka"]).exit_code == 1
        assert sm.ledger.codes.find("nope") is None
        assert all(sm.ledger.codes.canonical(i) == i for i in ids.values())


# ── the dialog ───────────────────────────────────────────────────────────

GEN = "c_" + "0" * 32


def _press(modal, key, character=None):
    modal.on_key(SimpleNamespace(key=key, character=character, stop=lambda: None))


def _type(modal, text):
    for ch in text:
        _press(modal, ch, ch)


def _cid(n):
    return f"c_{n:032d}"


def _rows():
    rows = [CodeRow(_cid(1), "shirka", "human", [], 100.0, 10.0, 0.0, 4, 1)]
    rows += [CodeRow(_cid(10 + i), f"shirka{i}", "default", [], 1.5 * i, 0.0, 0.0, 2, 0)
             for i in range(2, 6)]
    rows += [CodeRow(_cid(2), "sharpen", "human", ["shiny"], 50.0, 0.0, 0.0, 1, 0),
             CodeRow(GEN, "general", "human", [], 0.5, 0.0, 0.0, 1, 0),
             CodeRow(_cid(3), "shirka-old", "default", [], 0.0, 0.0, 0.0, 0, 0,
                     merged_into="shirka")]
    return sorted(rows, key=lambda r: (-r.total, r.name))


def _modal(inner=96):
    m = BillingCodesModal(id="bc")
    m.show()
    m._inner_width = inner
    posted = []
    m.post_message = posted.append
    m._hide = lambda: None
    m.set_rows(_rows(), settings={"new_agent_code": "name", "default_code": "general",
                                  "warnings": []}, default_id=GEN)
    return m, posted


def _at(m, name):
    m.selected_index = [r.name for r in m.shown].index(name)


class TestMarkShown:
    def test_star_marks_what_the_filter_shows_and_again_unmarks(self):
        m, _ = _modal()
        _press(m, "slash")
        _type(m, "shirka")
        _press(m, "enter")
        _press(m, "asterisk", "*")
        assert m.marks == {_cid(10 + i) for i in range(2, 6)} | {_cid(1)}
        assert "marked 5 shown codes · 5 marked, $121.00 · M merges" in m.render().plain
        _press(m, "asterisk", "*")
        assert m.marks == set() and "unmarked 5 shown codes" in m.render().plain

    def test_star_skips_the_default_and_merged_codes(self):
        m, _ = _modal()
        _press(m, "m", "m")  # merged rows shown
        _press(m, "asterisk", "*")
        assert GEN not in m.marks and _cid(3) not in m.marks and len(m.marks) == 6


class TestMergePicker:
    def test_m_on_the_focused_code_types_ahead_and_confirms(self):
        m, posted = _modal()
        _at(m, "shirka3")
        _press(m, "M", "M")
        assert m.mode == "merge"
        names = [r.name for r in m.candidates()]
        assert "shirka3" not in names and "shirka-old" not in names  # itself; merged
        assert "general" in names                                    # the default can be a target
        _type(m, "sh")
        assert [r.name for r in m.candidates()][:2] == ["shirka", "sharpen"]
        _type(m, "a")
        assert [r.name for r in m.candidates()] == ["sharpen"]
        _press(m, "backspace")
        _type(m, "i")
        # names starting with the query first; a previous name matches too
        assert [r.name for r in m.candidates()] == ["shirka", "shirka5", "shirka4", "shirka2",
                                                    "sharpen"]
        _press(m, "enter")
        assert m._target.name == "shirka" and posted == []
        assert "merge shirka3 ($4.50, 2 agents) into shirka? ↵ confirm · esc cancel" \
            in m.render().plain
        _press(m, "enter")
        msg = posted[-1]
        assert isinstance(msg, BillingCodesModal.MergeRequested)
        assert (msg.code_ids, msg.into_id, msg.into_name) == ([_cid(13)], _cid(1), "shirka")
        assert m.mode == "list"

    def test_m_on_marked_codes_states_the_totals(self):
        m, posted = _modal()
        for name in ("shirka2", "shirka3", "shirka4", "shirka5"):
            _at(m, name)
            _press(m, "space", " ")
        _press(m, "M", "M")
        _type(m, "shirka")
        assert [r.name for r in m.candidates()] == ["shirka"]  # the marked ones aren't offered
        _press(m, "enter")
        assert ("merge 4 codes ($21.00, 8 agents) into shirka? ↵ confirm · esc cancel"
                in m.render().plain)
        _press(m, "escape")  # back to the picker
        assert m.mode == "merge" and m._target is None and posted == []
        _press(m, "enter")
        _press(m, "enter")
        assert sorted(posted[-1].code_ids) == [_cid(12), _cid(13), _cid(14), _cid(15)]
        assert m.marks == set()

    def test_arrows_choose_and_an_exact_name_wins(self):
        m, posted = _modal()
        _at(m, "shirka2")
        _press(m, "M", "M")
        _press(m, "down")
        _press(m, "enter")
        assert m._target.name == m.candidates()[1].name
        m, _ = _modal()
        _at(m, "shirka2")
        _press(m, "M", "M")
        _type(m, "shirka")  # shirka, shirka3... all match; the exact name is picked
        _press(m, "down")
        _press(m, "enter")
        assert m._target.name == "shirka"

    def test_never_makes_a_code_nor_merges_into_a_source(self):
        m, posted = _modal()
        _at(m, "shirka2")
        _press(m, "space", " ")
        _at(m, "shirka3")
        _press(m, "space", " ")
        _press(m, "M", "M")
        _type(m, "brand-new")
        _press(m, "enter")
        assert m._target is None and "merge never makes a code" in m.render().plain
        m.query = "shirka3"
        _press(m, "enter")
        assert m._target is None and "one of the codes being merged" in m.render().plain
        _press(m, "escape")
        assert m.mode == "list" and posted == []

    def test_the_default_code_is_not_merged_away(self):
        m, _ = _modal()
        _at(m, "general")
        _press(m, "M", "M")
        assert m.mode == "list" and "can't be merged away" in m.render().plain

    def test_hints_list_the_keys(self):
        m, _ = _modal()
        hints = m.hints()
        for key in ("r rename", "M merge", "c collapse", "u unmerge", "/ filter",
                    "* mark shown", "space mark"):
            assert key in hints
        assert cell_len(hints) <= m.inner_width
        _press(m, "question_mark", "?")
        assert "s settings" in m.render().plain

    @pytest.mark.parametrize("cols", [120, 160, 200, 260])
    @pytest.mark.parametrize("state", ["picker", "typed", "confirm", "none"])
    def test_every_line_is_the_dialogs_width(self, cols, state):
        m, _ = _modal(inner=min(BillingCodesModal.WIDTH, cols - 2) - 4)
        m.rows.append(CodeRow("c_" + "9" * 32, "a-very-long-billing-code-name-" * 3,
                              total=123456.78, this_month=99999.99, agents=1234, running=56))
        _at(m, "shirka2")
        _press(m, "M", "M")
        if state == "typed":
            _type(m, "very")
        elif state == "confirm":
            _type(m, "a-very")
            _press(m, "enter")
        elif state == "none":
            _type(m, "zzz")
        lines = m.render().plain.split("\n")
        assert all(cell_len(line) == m.inner_width for line in lines), lines


# ── the app ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_shown_then_merge_from_the_tui(make_tui):
    from tests.tui_harness import ready

    sm = SessionManager(skip_git_detection=True)
    made = [sm.create_session(name=n, tmux_session="test", tmux_window=f"{n}-w",
                              command=["claude"], start_directory="/tmp")
            for n in ("shirka", "shirka2", "shirka3", "zed")]
    ids = [sm.get_session(s.id).billing_code_id for s in made]
    sm.ledger.append([_row(i, cost, s.id, s.name)
                      for i, cost, s in zip(ids, (10.0, 2.0, 3.0, 1.0), made)])
    app = make_tui()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        app.action_open_billing_codes()
        for _ in range(2):
            await app.workers.wait_for_complete()
            await pilot.pause()
        modal = app.query_one("#billing-codes-modal", BillingCodesModal)
        await pilot.press("slash", *"shirka", "enter", "asterisk")
        await pilot.press("space")   # unmark shirka itself, the target
        await pilot.press("escape")  # clears the filter; the marks stay
        assert modal.marks == set(ids[1:3]) and len(modal.shown) == 4
        await pilot.press("M", *"shirka", "enter")
        assert "merge 2 codes ($5.00, 2 agents) into shirka? ↵ confirm" in modal.render().plain
        await pilot.press("enter")
        for _ in range(2):
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert [(r.name, r.total) for r in modal.shown] == [("shirka", 15.0), ("zed", 1.0)]
        assert "merged 2 codes into shirka · undo: m then u" in modal.render().plain
    fresh = SessionManager(skip_git_detection=True)
    assert fresh.get_session(made[1].id).billing_code_id == ids[0]
    assert fresh.get_session(made[2].id).billing_code == "shirka"
