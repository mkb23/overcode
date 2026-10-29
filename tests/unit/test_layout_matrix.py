"""The agent table's layout at real terminal widths (the layout matrix).

Pilot tests elsewhere run at 420 columns, where nothing is ever cropped,
so a row that wraps at 120 columns passes them all. This sweeps realistic
widths x every detail level x every summary content mode, with an
expanded parent's delegated-work rows (and a member's detail line) open,
and checks ``assert_layout_invariants`` after every change: one screen row
per line, nothing past the edge, the cells of every row on one grid.

``OVERCODE_LAYOUT_SHOTS=<dir>`` also saves an SVG screenshot of each
state into <dir>, to look at. Nothing is written by default.
"""

import os
import re

import pytest

from tests.tui_harness import assert_layout_invariants, three_agents
from tests.unit.test_swarm_grid_tui import GridEngine, _open

pytestmark = pytest.mark.unit

WIDTHS = [120, 160, 200, 260]


def _shot(app, name: str) -> None:
    shots = os.environ.get("OVERCODE_LAYOUT_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        app.save_screenshot(f"{name}.svg", path=shots)


async def _settle(pilot) -> None:
    await pilot.pause()
    await pilot.pause()


def _worktree(name: str, repo: str) -> dict:
    return {"name": name, "path": f"/src/{repo}-wt/{name}", "main": f"/src/{repo}", "repo": repo}


def _named_agents():
    """three_agents with every kind of name: dim default, "~" auto, plain human (#542),
    and every repo cell (#544): a main checkout, a worktree named after its
    branch (repo⎇) and one that is not (repo⎇name)."""
    agents = three_agents()
    for session, source in zip(agents, ("default", "auto", "human")):
        session.name_source = source
    agents[1].worktree = _worktree("main", "repo-b")
    agents[2].worktree = _worktree("wt-544", "repo-c")
    return agents


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_at_every_detail_level_and_mode(make_tui, width):
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        rows = await _open(pilot, engine)
        # A member's detail line too
        app.swarm_cursor = "id-bravo/sa/a1"
        app._sync_swarm_rows()
        await pilot.press("right")
        await _settle(pilot)
        assert any(ln.kind == "detail" for ln in rows.lines)
        assert_layout_invariants(app)

        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await _settle(pilot)
            for _ in app.SUMMARY_CONTENT_MODES:
                mode = app.summary_content_mode
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, {mode}: {e}") from e
                _shot(app, f"layout-{width}-{level}-{mode}")
                app.action_cycle_summary_content()
                await _settle(pilot)

        # Emoji-free at the widest detail
        app.action_toggle_emoji_free()
        await _settle(pilot)
        try:
            assert_layout_invariants(app)
        except AssertionError as e:
            raise AssertionError(f"{width} cols, emoji-free: {e}") from e
        _shot(app, f"layout-{width}-emoji-free")


async def test_layout_follows_a_resize(make_tui):
    """Narrowing the terminal re-crops every row; widening it again restores them."""
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(260, 40))
    async with app.run_test(size=(260, 40)) as pilot:
        await _open(pilot, engine)
        app._set_summary_level("full")
        await _settle(pilot)
        for width in (120, 200, 260):
            await pilot.resize_terminal(width, 40)
            await _settle(pilot)
            try:
                assert_layout_invariants(app)
            except AssertionError as e:
                raise AssertionError(f"resized to {width}: {e}") from e


class TripEngine(GridEngine):
    """GridEngine plus every spend cell's variants: alpha's trip reset (↺ on
    TOK / ENRG / $ / Q%), bravo's SUB$ on trips, quotas and a window burn
    with its quota points (the burn's Q% mode)."""

    def snapshot_now(self):
        snap = super().snapshot_now()
        burn = {str(float(h)): {"input_tokens": 9000, "output_tokens": 3000, "cost_usd": 1.2,
                                "energy_j": 900.0, "quota_pp": 0.84}
                for h in (1, 3, 6, 12, 24)}
        for view in snap.agents.values():
            view.update(quota={"pp7d": 12.34, "pp5h": 3.1, "w7": 40, "w5": 10, "conf": "low"},
                        burn=burn, estimated_energy_j=5.0e5)
        snap.agents["id-alpha"]["trip"] = {
            "reset_at": "2026-10-07T10:00:00+00:00", "estimated_cost_usd": 0.02,
            "estimated_energy_j": 900.0, "input_tokens": 10, "output_tokens": 5,
            "cache_creation_tokens": 0, "cache_read_tokens": 0, "pp7d": 0.01}
        snap.agents["id-bravo"].update(subtree_cost_usd=3.0, subtree_trip_cost_usd=1.0)
        return snap


def _trip_agents():
    agents = _named_agents()
    agents[0].cost_budget_usd = 5.0  # BDG shows in the $ modes
    return agents


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_trip_counters_in_every_cost_mode(make_tui, width):
    """A reset row (↺) and the burn's quota mode keep the grid, in every $ preset."""
    engine = TripEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_trip_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        marked = set()
        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await _settle(pilot)
            for _ in app._COST_PRESETS:
                app.action_toggle_cost_display()
                await _settle(pilot)
                mode = app.show_cost
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, {mode}: {e}") from e
                _shot(app, f"layout-trip-{width}-{level}-{mode}")
                alpha = next(w for w in app._get_widgets_in_session_order()
                             if w.session.name == "alpha")
                line = alpha.render().plain
                if "↺" in line:
                    marked.add(mode)
                if mode == "quota" and level in ("high", "full") and width >= 200:
                    # 0.84 points over the burn window, in points per hour
                    assert re.search(r"🔥\d+\.\d\d%/h", line), line
        assert {"tokens", "cost", "joules", "quota"} <= marked

        app.action_toggle_emoji_free()
        await _settle(pilot)
        try:
            assert_layout_invariants(app)
        except AssertionError as e:
            raise AssertionError(f"{width} cols, emoji-free: {e}") from e


# A child, with an auto name and a reported failure
NESTED_NAME = "fix-flaky-integration-tests-main"
REASON = "three integration tests still fail on main"


class NestedEngine(GridEngine):
    """GridEngine plus delta (alpha's child) with a result."""

    def snapshot_now(self):
        from tests.unit.test_swarm_tui import _view
        snap = super().snapshot_now()
        snap.agents["id-delta"] = _view("delta", 3,
                                        current_status="waiting_oversight",
                                        task_result="failure", task_reason=REASON)
        return snap


def _nested_agents():
    from overcode.session_manager import Session
    agents = _named_agents()
    agents.append(Session(id="id-delta", name=NESTED_NAME, tmux_session="test", tmux_window="w3",
                          command=["claude"], start_directory="/tmp",
                          start_time="2026-09-24T10:00:00", repo_name="repo-a", branch="main",
                          parent_session_id="id-alpha", name_source="auto"))
    return agents


def _row_text(app, name: str) -> str:
    row = next(w for w in app._get_widgets_in_session_order() if w.session.name == name)
    return row.render().plain


@pytest.mark.parametrize("width", WIDTHS)
async def test_nested_names_and_the_result_mode_hold(make_tui, width):
    """Tree sort with a nested auto-named child that reported: the NAME
    column makes room for its prefix and marker, and the result mode shows
    its glyph and reason at the end of the row."""
    from overcode.settings import TUIPreferences
    from overcode.tui_widgets.session_summary import NAME_WIDTH_CAPS

    saved = TUIPreferences()
    saved.sort_mode = "by_tree"
    saved.save("test")
    engine = NestedEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_nested_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        await _settle(pilot)
        # Prefix, "~" and the whole name: the width the column asks for
        assert app.max_name_width >= len("└─~" + NESTED_NAME)
        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await _settle(pilot)
            # Within the cap, the nested name is whole
            if NAME_WIDTH_CAPS[level] >= len("└─~" + NESTED_NAME):
                assert NESTED_NAME in _row_text(app, NESTED_NAME), level
            for _ in app.SUMMARY_CONTENT_MODES:
                mode = app.summary_content_mode
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, {mode}: {e}") from e
                if mode == "result":
                    summary = _row_text(app, NESTED_NAME).split(" │ ")[-1]
                    # Whatever fits of it, never nothing
                    assert summary.startswith("❌ three"), (width, level, summary)
                    assert _row_text(app, "bravo").split(" │ ")[-1].startswith("🏁")
                _shot(app, f"nested-{width}-{level}-{mode}")
                app.action_cycle_summary_content()
                await _settle(pilot)


@pytest.mark.parametrize("width", [120, 200])
@pytest.mark.parametrize("preset", ["calm", "colorblind", "shapes"])
async def test_layout_holds_in_every_status_theme(make_tui, width, preset, tmp_path, monkeypatch):
    """A theme's glyph keeps the status cell two wide, one-cell shapes included,
    at every detail level, with and without emoji."""
    from overcode.status_theme import StatusTheme, save_theme
    # Its own config.yaml: a saved theme must not outlive the test
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    save_theme(StatusTheme(preset))
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{preset}, {width} cols, {level}, "
                                         f"emoji-free {emoji_free}: {e}") from e
                _shot(app, f"layout-{preset}-{width}-{level}-{'ascii' if emoji_free else 'emoji'}")


class WorkerEngine(GridEngine):
    """GridEngine with alpha's conversation in Claude's background worker:
    its status detail carries the 🌙 badge, beside a tool and a 🔀."""

    def snapshot_now(self):
        snap = super().snapshot_now()
        snap.agents["id-alpha"]["status_detail"] = {
            "color": "color_green", "legacy_status": "running",
            "badges": [{"kind": "tool"}, {"kind": "bg_worker", "label": "df7bb2f5"},
                       {"kind": "conv_adopted", "label": "b08316a9"}]}
        snap.agents["id-bravo"]["status_detail"] = {
            "color": "color_rest", "legacy_status": "waiting_user",
            "badges": [{"kind": "bg_worker", "label": "0f0f0f0f"}]}
        return snap


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_the_background_worker_badge(make_tui, width):
    engine = WorkerEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        seen = set()
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, emoji-free {emoji_free}: "
                                         f"{e}") from e
                _shot(app, f"layout-worker-{width}-{level}-{emoji_free}")
                bravo = next(w for w in app._get_widgets_in_session_order()
                             if w.session.name == "bravo")
                line = bravo.render().plain
                seen |= {mark for mark in ("🌙", "Bw") if mark in line}
        assert seen == {"🌙", "Bw"}


class TitleLagEngine(GridEngine):
    """GridEngine with charlie (the widest name) and bravo (an auto "~" name)
    renamed since Claude last showed their names: the "*" mark."""

    lag = True

    def snapshot_now(self):
        snap = super().snapshot_now()
        if self.lag:
            snap.agents["id-charlie"]["cli_title_lag"] = "old-charlie"
            snap.agents["id-bravo"]["cli_title_lag"] = "old-bravo"
        return snap


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_a_lagging_cli_title(make_tui, width):
    """The "*" trails the whole name (the widest is never cut for it) and
    goes, with its cell, when the title lands."""
    engine = TitleLagEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))

    def line(name):
        return next(w for w in app._get_widgets_in_session_order()
                    if w.session.name == name).render().plain

    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, emoji-free {emoji_free}: "
                                         f"{e}") from e
                _shot(app, f"layout-titlelag-{width}-{level}-{emoji_free}")
                assert "charlie*" in line("charlie"), level
                assert "~bravo*" in line("bravo"), level
                assert "alpha*" not in line("alpha")
        engine.lag = False
        engine.tick()
        await _settle(pilot)
        assert_layout_invariants(app)
        assert "charlie*" not in line("charlie") and "charlie" in line("charlie")



class WorkflowEngine(GridEngine):
    """GridEngine with Workflow runs (#498): bravo is in phase 2 of 3 of a
    long-named run with a second going; charlie has only a finished run
    (its WF cell is the placeholder)."""

    def snapshot_now(self):
        snap = super().snapshot_now()
        running = {"name": "review-changes-across-the-whole-repo", "status": "running",
                   "started": 128, "done": 97, "failed": 0, "phase": "Verify",
                   "phase_index": 2, "phase_count": 3, "source": "journal",
                   "phases": [{"title": "Find", "started": 30, "done": 30},
                              {"title": "Verify", "started": 98, "done": 67},
                              {"title": "Report", "started": 0, "done": 0}]}
        snap.agents["id-bravo"]["workflow_progress"] = [
            dict(running, name="second", phase="Undeclared-phase-title",
                 phase_index=None, phase_count=None), running]
        snap.agents["id-charlie"]["workflow_progress"] = [
            {"name": "lint", "status": "ok", "started": 2, "done": 2, "failed": 0,
             "source": "final"}]
        return snap


@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_workflow_progress(make_tui, width, expanded):
    """The WF cell (#498) at every width, detail level, content mode, with
    and without emoji: bravo's running run, charlie's placeholder. With
    bravo collapsed it shows at every width; expanded, the TYPE column its
    member rows bring takes the room at 120 columns, and the grid crops WF
    (never the name)."""
    engine = WorkflowEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        if expanded:
            await _open(pilot, engine)
        await _settle(pilot)
        seen = set()
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                for _ in app.SUMMARY_CONTENT_MODES:
                    mode = app.summary_content_mode
                    try:
                        assert_layout_invariants(app)
                    except AssertionError as e:
                        raise AssertionError(f"{width} cols, {level}, {mode}, expanded "
                                             f"{expanded}, emoji-free {emoji_free}: {e}") from e
                    _shot(app, f"layout-workflow-{width}-{level}-{mode}-{expanded}-{emoji_free}")
                    app.action_cycle_summary_content()
                    await _settle(pilot)
                bravo = _row_text(app, "bravo")
                assert "bravo" in bravo, (level, bravo)
                if not expanded or width >= 160:
                    assert "2/3 · 97/128" in bravo and "+1" in bravo, (level, bravo)
                seen |= {mark for mark in ("🧩 review-", "Wk review-", "🧩 2/3", "Wk 2/3")
                         if mark in bravo}
                assert "lint" not in _row_text(app, "charlie")
        if not expanded or width >= 160:
            assert seen == {"🧩 review-", "Wk review-", "🧩 2/3", "Wk 2/3"}


def test_fit_widths_drops_droppable_columns_rightmost_first():
    from overcode.columns.layout import SUMMARY_BAR_WIDTH, SUMMARY_MIN_WIDTH, fit_widths
    width = 30 + SUMMARY_BAR_WIDTH + SUMMARY_MIN_WIDTH  # room for 30 cells
    widths = [10, 8, 8, 8, 6]
    assert fit_widths(widths, [False] * 5, width) == widths  # nothing to drop: cut as before
    assert fit_widths(widths, [False, True, False, True, True], width) == [10, 8, 8, 0, 0]
    assert fit_widths([10, 8], [True, True], width) == [10, 8]  # fits: untouched


@pytest.mark.parametrize("width", [200, 250])
async def test_full_keeps_the_columns_high_shows_when_it_does_not_fit(make_tui, width):
    """Full on a row too narrow for every column: the columns only full
    adds (hidden at high) give way, rightmost first, instead of whatever
    sits at the grid's right end. CTX and the model stay, and the header
    says how many are hidden."""
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    app._prefs.column_config = {"high": {c: False for c in (
        "subagents", "pr_number", "stalled_time", "sleep_time", "active_pct",
        "human_count", "robot_count", "heartbeat", "oversight_countdown", "agent_value")}}
    async with app.run_test(size=(width, 40)) as pilot:
        app._set_summary_level("full")
        await _settle(pilot)
        assert_layout_invariants(app)
        _shot(app, f"layout-full-fit-{width}")
        header = app.query_one("#column-headers").render().plain
        dropped = [n for n, w in zip(app._natural_column_widths, app.column_widths) if n and not w]
        assert dropped and f"+{len(dropped)} hidden" in header, header
        for label in (" CTX", " MDL"):
            assert label in header, (label, header)
        assert "📚" in _row_text(app, "alpha")
        assert sum(app.column_widths) < sum(app._natural_column_widths)


class WeftEngine(GridEngine):
    """GridEngine with weft claims: bravo holds two (one stuck, with a long
    title), charlie one at work; alpha's WFT cell is the placeholder."""

    def snapshot_now(self):
        snap = super().snapshot_now()
        lease = {"since": "2026-10-28T12:00:00Z", "until": "2026-10-28T13:00:00Z",
                 "actor": "x", "project": "useful-bit"}
        snap.agents["id-bravo"]["weft_claims"] = [
            dict(lease, task="t-3c9e01", status="stuck",
                 title="Rerun the uncertainty-shapes tranche at zero cost on spark"),
            dict(lease, task="us-fixes", status="working", title="Fix the inspector")]
        snap.agents["id-charlie"]["weft_claims"] = [
            dict(lease, task="t-0a1b2c", status="working", title="Sweep")]
        return snap


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_weft_claims(make_tui, width):
    """The WFT cell at every width, detail level, content mode, with and
    without emoji: bravo's stuck claim (+1), charlie's, alpha's placeholder."""
    engine = WeftEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _settle(pilot)
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                for _ in app.SUMMARY_CONTENT_MODES:
                    mode = app.summary_content_mode
                    try:
                        assert_layout_invariants(app)
                    except AssertionError as e:
                        raise AssertionError(f"{width} cols, {level}, {mode}, "
                                             f"emoji-free {emoji_free}: {e}") from e
                    _shot(app, f"layout-weft-{width}-{level}-{mode}-{emoji_free}")
                    app.action_cycle_summary_content()
                    await _settle(pilot)
                bravo = _row_text(app, "bravo")
                assert "t-3c9e01" in bravo and "stuck +1" in bravo, (width, level, bravo)
                assert "t-0a1b2c" in _row_text(app, "charlie"), (width, level)
