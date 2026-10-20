"""Delegated-work rows on the agent column grid (0.6.1 observability, phase 2).

An expanded parent's delegated agents and summary lines render
through the summary columns (``ColumnContext.for_virtual``,
``columns.render_virtual``) and are padded to the same aligned widths as
the agent rows, so every cell lines up; a column that does not apply shows
a dim "-". Pilot tests on the ``make_tui`` harness, plus the pure parts.
"""

import pytest
from rich.cells import cell_len

from overcode import swarm, swarm_view
from overcode.columns import render_virtual
from overcode.engine_protocol import Snapshot
from overcode.summary_columns import SUMMARY_COLUMNS, ColumnContext, cell_segments
from tests.tui_harness import FakeEngine, three_agents
from tests.unit.test_swarm_tui import NOW, ROLLUP, _on_bravo, _view

pytestmark = pytest.mark.unit


class Details:
    """The meta files and the workflow journal beside bravo's transcript."""

    def meta(self, agent_id, run_id):
        return {"a1": {"description": "map the engine", "agentType": "Explore",
                       "model": "claude-haiku-4-5"}}.get(agent_id)

    def journal(self, run_id):
        return [{"type": "started", "agentId": "w1", "label": "verify-1", "phase": "Verify"},
                {"type": "started", "agentId": "w2", "label": "find-1", "phase": "Find"},
                {"type": "result", "agentId": "w2", "result": "found 3 call sites"}]


def _hook_state():
    return {
        "event": "Stop", "timestamp": NOW,
        "subagents": {
            "a1": {"kind": "subagent", "type": "Explore", "started_at": NOW - 30,
                   "activity": "Read src/engine.py"},
            "w1": {"kind": "workflow", "started_at": NOW - 20, "waiting": True, "run": "wf_1"},
        },
        "swarm": {"bg_at": NOW,
                  "workflows": {"t1": {"status": "running", "name": "review", "run_id": "wf_1"}},
                  "done": {"a2": {"kind": "subagent", "result": "failed", "ended_at": NOW - 5,
                                  "started_at": NOW - 50},
                           "w2": {"kind": "workflow", "result": "ok", "ended_at": NOW - 3,
                                  "started_at": NOW - 40, "run": "wf_1"}}},
    }


CONVERSATIONS = {"sid": {"cost": 4.0},
                 "sid/agent-a1": {"cost": 0.5, "in": 12000, "out": 3300},
                 "sid/workflows/wf_1/agent-w1": {"cost": 1.25}}


class GridEngine(FakeEngine):
    """Publishes bravo's rows (spend, models, a workflow with phases) while subscribed."""

    def __init__(self):
        super().__init__()

    def snapshot_now(self):
        agents = {}
        for i, name in enumerate(("alpha", "bravo", "charlie")):
            extra = dict(swarm_rollup=ROLLUP, subagent_cost_usd=1.75) if name == "bravo" else {}
            # Two models in the fleet, so MDL shows (it hides while uniform)
            model = "claude-sonnet-4-6" if name == "alpha" else "claude-opus-4-6"
            view = _view(name, i, model=model, **extra)
            agents[view["session_id"]] = view
        virtual = {}
        if "id-bravo" in self.swarm_subscriptions:
            virtual, _ = swarm.virtual_rows("id-bravo", _hook_state(), NOW, 30, Details(),
                                            CONVERSATIONS)
        return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                              "interval_mode": "attended"}, virtual=virtual)

    def tick(self):
        self.publish(self.snapshot_now())


@pytest.fixture
def engine():
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    return engine


@pytest.fixture
def app(make_tui, engine):
    return make_tui(three_agents(), engine=engine, size=(420, 40))


async def _open(pilot, engine):
    await _on_bravo(pilot)
    await pilot.press("right")
    engine.tick()
    await pilot.pause()
    return pilot.app._swarm_widgets["id-bravo"]


def _bravo(app):
    return next(w for w in app._get_widgets_in_session_order() if w.session.name == "bravo")


def _plain_lines(rows):
    return rows._shown.plain.split("\n")


def _at(plain: str, x: int) -> str:
    """``plain`` from cell offset ``x`` on."""
    w = 0
    for i, ch in enumerate(plain):
        if w >= x:
            return plain[i:]
        w += cell_len(ch)
    return ""


def _x_of(plain: str, needle: str) -> int:
    return cell_len(plain[:plain.index(needle)])


def _line(rows, key):
    i = next(i for i, ln in enumerate(rows.lines) if ln.key == key)
    return i, _plain_lines(rows)[i]


def _cell(rows, key, col_id):
    i = next(i for i, ln in enumerate(rows.lines) if ln.key == key)
    j = [c.id for c in rows._columns].index(col_id)
    return "".join(t for t, _ in rows._cells[i][j])


@pytest.mark.asyncio
class TestAlignment:
    async def test_every_cell_starts_where_the_agent_row_s_does(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            bravo = _bravo(app)
            agent = bravo.render().plain
            cols = [c.id for c in bravo.visible_columns()]
            widths = app.column_widths
            assert len(widths) == len(cols)
            for col in ("status_symbol", "time_in_state", "subagents", "agent_name",
                        "uptime", "token_count", "model", "git_diff", "context_usage"):
                assert col in cols, col
            checked = 0
            for i, line in enumerate(rows.lines):
                if line.kind == "detail":
                    continue
                vplain = _plain_lines(rows)[i]
                # The summary column's bar is at the same x on every row
                assert _x_of(vplain, " │ ") == _x_of(agent, " │ "), line.key
                for j, col in enumerate(cols):
                    text = "".join(t for t, _ in rows._cells[i][j])
                    if not text.strip():
                        continue
                    x = sum(widths[:j])
                    assert _at(vplain, x).startswith(text), (line.key, col)
                    checked += 1
                # The agent row's cell at that x too (the same grid)
                for j, cell in enumerate(bravo.row_cells()):
                    if cell.plain.strip():
                        assert _at(agent, sum(widths[:j])).startswith(cell.plain)
            assert checked > 20

    async def test_a_member_fills_what_it_has(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            a1 = "id-bravo/sa/a1"
            assert _cell(rows, a1, "status_symbol").startswith("🟢")
            assert "👥 map the engine" in _cell(rows, a1, "agent_name")
            assert _cell(rows, a1, "model").strip() == "Hk4.5"
            assert _cell(rows, a1, "token_count").strip() == "Σ 15.3K"
            assert _cell(rows, a1, "time_in_state").strip().endswith("d")  # since it started
            # A finished one: its run time, held still (its result is the
            # summary's result mode, TestSummaryModes)
            a2 = "id-bravo/sa/a2"
            assert "task_result" not in [c.id for c in rows._columns]
            assert _cell(rows, a2, "time_in_state").strip() == "45s"
            # Columns that do not apply: nothing of the parent's leaks in
            assert _cell(rows, a1, "git_diff") == "" and _cell(rows, a1, "repo_name") == ""
            assert "repo-b" not in _line(rows, a1)[1]

    async def test_the_name_column_widens_for_the_titles_and_back(self, app, engine):
        async with app.run_test() as pilot:
            await pilot.pause()
            before = app.max_name_width
            await _open(pilot, engine)
            assert app.max_name_width > before
            _, text = _line(app._swarm_widgets["id-bravo"], "id-bravo/sa/a1")
            assert "map the engine" in text
            await pilot.press("left")
            await pilot.pause()
            assert app.max_name_width == before

    async def test_the_detail_line_starts_at_the_name_column(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            app.swarm_cursor = "id-bravo/sa/a1"
            app._sync_swarm_rows()
            await pilot.press("right")
            i, detail = next((i, ln) for i, ln in enumerate(rows.lines) if ln.kind == "detail")
            cols = [c.id for c in rows._columns]
            name_x = sum(app.column_widths[:cols.index("agent_name")])
            text = _plain_lines(rows)[i]
            assert text[:name_x].strip() == ""
            assert _at(text, name_x).startswith(detail.connector + "claude-haiku-4-5 · Explore")
            assert "last: Read src/engine.py" in text and "Explore" in text


class TestPlaceholders:
    @pytest.mark.asyncio
    async def test_columns_that_do_not_apply_show_a_dim_dash(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            i, _ = _line(rows, "id-bravo/sa/a1")
            line = rows._shown.split("\n")[i]
            cols = [c.id for c in rows._columns]
            j = cols.index("git_diff")
            x = sum(app.column_widths[:j])
            edge = app.column_edges[j]
            assert edge > 0
            dash = _char_index(line.plain, x + edge - 1)
            assert line.plain[dash] == "-"
            styles = [str(s.style) for s in line.spans if s.start <= dash < s.end]
            assert any("dim" in s for s in styles)

    def test_placeholder_shape(self):
        assert render_virtual.placeholder(0) == []
        assert render_virtual.placeholder(6) == [("     -", "dim")]

    def test_edges_ignore_trailing_spaces(self):
        from rich.text import Text
        assert render_virtual.column_edges([[Text(" 12m "), Text("  ")],
                                            [Text("  3s "), Text("  ")]]) == [4, 0]


def _char_index(plain: str, x: int) -> int:
    w = 0
    for i, ch in enumerate(plain):
        if w >= x:
            return i
        w += cell_len(ch)
    return len(plain)


class TestFlatLines:
    @pytest.mark.asyncio
    async def test_members_sit_right_under_the_agent_and_nothing_else(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            keys = [ln.key for ln in rows.lines]
            # No group, phase or done lines: 🟠 first, then by start; finished just now.
            # The finished count is the agent's own SubAgents cell
            assert keys == ["id-bravo/wf/wf_1/w1", "id-bravo/sa/a1",
                            "id-bravo/wf/wf_1/w2", "id-bravo/sa/a2"]
            assert {ln.depth for ln in rows.lines} == {1}
            assert rows.lines[-1].connector.endswith("└─")
            assert "🧩 verify-1" in _cell(rows, "id-bravo/wf/wf_1/w1", "agent_name")

    @pytest.mark.asyncio
    async def test_the_type_column_shows_each_member_s_type(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            assert app.swarm_types_shown and app.fleet.swarm_types_shown
            assert _cell(rows, "id-bravo/sa/a1", "agent_type").strip() == "Explore"
            assert _cell(rows, "id-bravo/wf/wf_1/w1", "agent_type").strip() == "review · Verify"
            assert _cell(rows, "id-bravo/sa/a2", "agent_type").strip() == "subagent"
            # The agent rows show their own kind, on the same grid
            bravo = _bravo(app)
            cols = [c.id for c in bravo.visible_columns()]
            j = cols.index("agent_type")
            assert bravo.row_cells()[j].plain.strip() == "agent"
            x = sum(app.column_widths[:j])
            assert _at(_line(rows, "id-bravo/sa/a1")[1], x).startswith(" Explore")
            assert _at(bravo.render().plain, x).startswith(" agent")
            # Collapsed again: the column takes no width
            await pilot.press("left")
            await pilot.pause()
            assert not app.fleet.swarm_types_shown
            assert app.column_widths[j] == 0
            assert bravo.row_cells()[j].plain == ""

    def test_a_large_list_s_more_line_counts_in_suba(self):
        rows = {f"p/sa/a{i}": {"parent": "p", "row": "member", "group": "p/sa",
                               "kind": "subagent", "light": "green", "started_at": float(i)}
                for i in range(12)}
        lines = swarm_view.layout("p", rows)
        more = lines[-1]
        cell = cell_segments(_column("subagents"), ColumnContext.for_virtual(more, _parent_ctx()))
        assert "".join(t for t, _ in cell).split() == ["+9"]

    def test_a_member_without_a_model_of_its_own_shows_the_parent_s_dim(self):
        line = swarm_view.SwarmLine("p/sa/x", "", "member", 1, label="t",
                                    row={"light": "green", "model_inherited": True})
        ctx = ColumnContext.for_virtual(line, _parent_ctx(model="claude-opus-4-6"))
        segs = cell_segments(_column("model"), ctx)
        assert "".join(t for t, _ in segs).strip() == "Op4.6"
        assert all(style == "dim" for _, style in segs)
        own = swarm_view.SwarmLine("p/sa/y", "", "member", 1, label="t",
                                   row={"light": "green", "model": "claude-haiku-4-5"})
        segs = cell_segments(_column("model"),
                             ColumnContext.for_virtual(own, _parent_ctx(model="claude-opus-4-6")))
        assert "".join(t for t, _ in segs).strip() == "Hk4.5"
        assert not any(style == "dim" for _, style in segs)

    def test_a_member_s_quota_and_billing_cells(self):
        line = swarm_view.SwarmLine("p/sa/x", "", "member", 1, label="t",
                                    row={"light": "green", "quota_pp": 1.25})
        ctx = ColumnContext.for_virtual(line, _parent_ctx())
        assert "".join(t for t, _ in cell_segments(_column("quota"), ctx)).strip() == "1.25%"
        bill = cell_segments(_column("billing_code"), ctx)
        assert bill and all(style == "dim" for _, style in bill)
        # A summary line has neither
        done = swarm_view.SwarmLine("p/+more", "", "more", 1, label="+3 more",
                                    counts=(("more", 3),))
        dctx = ColumnContext.for_virtual(done, _parent_ctx())
        assert cell_segments(_column("quota"), dctx) == []
        assert cell_segments(_column("billing_code"), dctx) == []


class TestSummaryModes:
    @pytest.mark.asyncio
    async def test_l_cycles_what_a_member_shows(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            seen = {}
            for _ in app.SUMMARY_CONTENT_MODES:
                _, text = _line(rows, "id-bravo/sa/a1")
                seen[app.summary_content_mode] = text.split(" │ ", 1)[1].strip()
                await pilot.press("l")
                await pilot.pause()
            assert seen["ai_short"] == "💬 Read src/engine.py"
            assert seen["ai_long"] == "📖 Explore · subagent · Read src/engine.py"
            assert seen["orders"] == "🎯 map the engine"
            assert seen["last_command"] == "⌨ Read src/engine.py"
            assert seen["annotation"] == seen["heartbeat"] == seen["result"] == "-"
            # A finished one's result: glyph and text
            while app.summary_content_mode != "result":
                await pilot.press("l")
            await pilot.pause()
            _, text = _line(rows, "id-bravo/sa/a2")
            assert text.split(" │ ", 1)[1].strip() == "❌ failed"

    def test_mapping_for_a_finished_member_and_a_summary_line(self):
        done = swarm_view.SwarmLine("k", "", "member", 2, label="t", summary="found it · ran 4s",
                                    row={"light": "done", "result": "ok"})
        assert render_virtual.virtual_summary(done, "ai_short") == ("💬 found it · ran 4s",
                                                                    "dim italic")
        assert render_virtual.virtual_summary(done, "last_command") == ("-", "dim")
        assert render_virtual.virtual_summary(done, "result") == ("✅ done", "green")
        failed = swarm_view.SwarmLine("k", "", "member", 2, label="t",
                                      row={"light": "done", "result": "failed",
                                           "result_text": "3 tests fail"})
        assert render_virtual.virtual_summary(failed, "result") == ("❌ 3 tests fail", "red")
        assert render_virtual.virtual_summary(failed, "result", emoji_free=True)[0] == "XX 3 tests fail"
        more = swarm_view.SwarmLine("g", "", "more", 1, summary="→ to open")
        assert render_virtual.virtual_summary(more, "heartbeat")[0] == "→ to open"


@pytest.mark.asyncio
class TestEmojiFree:
    async def test_no_emoji_on_the_virtual_rows(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            app.action_toggle_emoji_free()
            await pilot.pause()
            text = rows._shown.plain
            for glyph in ("🧩", "👥", "🟢", "🟠", "✅", "❌"):
                assert glyph not in text, glyph
            assert "Wk verify-1" in text and "Sa map the engine" in text
            bravo = _bravo(app)
            assert _x_of(_plain_lines(rows)[0], " │ ") == _x_of(bravo.render().plain, " │ ")


@pytest.mark.asyncio
class TestQuiet:
    async def test_the_clock_redraws_only_when_a_duration_moves(self, app, engine, monkeypatch):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            monkeypatch.setattr(render_virtual, "_now", lambda: NOW + 100)
            rows.tick()
            builds, paints = rows.builds, rows.paints
            for _ in range(5):
                app._tick_swarm_rows()
            assert rows.paints == paints and rows.builds == builds
            monkeypatch.setattr(render_virtual, "_now", lambda: NOW + 200)
            app._tick_swarm_rows()
            assert rows.paints == paints + 1 and rows.builds == builds
            _, text = _line(rows, "id-bravo/sa/a1")
            assert "3.8m" in text  # 230 s since it started
            _, done = _line(rows, "id-bravo/sa/a2")
            assert "45s" in done  # a finished member's run time holds still

    async def test_a_quiet_engine_rebuilds_nothing(self, app, engine):
        async with app.run_test() as pilot:
            rows = await _open(pilot, engine)
            builds, paints = rows.builds, rows.paints
            for _ in range(5):
                engine.tick()
                await pilot.pause()
            app.update_session_widgets()
            await pilot.pause()
            assert rows.builds == builds and rows.paints == paints


@pytest.mark.parametrize("width", [120, 200])
async def test_lines_never_wrap_at_any_detail_level(make_tui, width):
    """At a real terminal width the columns overrun the row at high detail:
    each line crops at the edge, one screen row per line, as an agent row
    does (they used to wrap and shift every row below)."""
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(three_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        rows = await _open(pilot, engine)
        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await pilot.pause()
            await pilot.pause()
            assert rows.outer_size.height == len(rows.lines), level
            assert all(cell_len(ln) <= rows.content_size.width
                       for ln in _plain_lines(rows)), level


# ---------------------------------------------------------------------------
# Every registered column with a virtual context
# ---------------------------------------------------------------------------

def _column(col_id):
    return next(c for c in SUMMARY_COLUMNS if c.id == col_id)


def _parent_ctx(**kw):
    from tests.unit.test_summary_columns import _make_ctx
    return _make_ctx(**kw)


def _sample_lines():
    rows, _ = swarm.virtual_rows("p", _hook_state(), NOW, 30, Details(), CONVERSATIONS)
    lines = swarm_view.layout("p", rows, opened={"p/done": True}, details={"p/sa/a1"})
    kinds = {ln.kind for ln in lines}
    assert {"member", "detail"} <= kinds and "done" not in kinds
    return [ln for ln in lines if ln.kind != "detail"]


@pytest.mark.parametrize("col", SUMMARY_COLUMNS, ids=lambda c: c.id)
@pytest.mark.parametrize("emoji_free", [False, True])
def test_every_column_renders_a_virtual_row_or_its_placeholder(col, emoji_free):
    parent = _parent_ctx(emoji_free=emoji_free)
    for line in _sample_lines():
        ctx = ColumnContext.for_virtual(line, parent)
        segs = cell_segments(col, ctx)
        assert isinstance(segs, list)
        assert all(isinstance(t, str) and isinstance(s, str) for t, s in segs)
        if col.virtual_render is None:
            assert segs == []  # the grid draws the placeholder
        if col.virtual_hover is not None:
            col.virtual_hover(ctx)


def test_a_virtual_context_carries_none_of_the_parent_s_agent_data():
    parent = _parent_ctx(swarm_rollup={"total": 1, "running": 1}, child_count=3,
                         pr_number=12, model="claude-opus-4-6")
    member = next(ln for ln in _sample_lines() if ln.key == "p/sa/a1")
    ctx = ColumnContext.for_virtual(member, parent)
    assert ctx.virtual is member and ctx.model == "claude-haiku-4-5"
    assert ctx.swarm_rollup is None and ctx.child_count == 0 and ctx.pr_number is None
    assert ctx.bg == "" and ctx.emoji_free == parent.emoji_free

