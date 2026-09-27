"""Tests for the learning journey (#483): mastery, catalog, curriculum, and the `u` panel."""

import inspect
import re

import pytest

from overcode import journey as J
from overcode.journey import (
    COMPETENCIES,
    Residue,
    compute_journey,
    mastery_for,
    render_journey,
)
from overcode.usage_analytics import summarize

NOW = 1_790_000_000_000.0
DAY = 86_400_000


def _keymap():
    from overcode.command_palette import keys_by_action
    from overcode.tui import SupervisorTUI
    return keys_by_action(SupervisorTUI.BINDINGS)


def _actions(*spec):
    """spec: (action, via, n) → records in one run."""
    recs, t = [], NOW - 1000
    for action, via, n in spec:
        for _ in range(n):
            t += 10
            recs.append({"kind": "action", "sid": "s", "t": t, "action": action, "via": via, "ok": True})
    return recs


def _journey(*spec, residue=None, now=NOW, **kw):
    return compute_journey(summarize(_actions(*spec)), residue or Residue(), _keymap(), now_ms=now, **kw)


class TestMastery:
    @pytest.mark.parametrize("uses,share,has_key,level", [
        (0, 0, True, "unaware"),
        (1, 1, True, "tried"),
        (3, 1, True, "fluent"),
        (5, 0.2, True, "habitual"),
        (5, 0, False, "habitual"),
        (8, 0, False, "fluent"),
    ])
    def test_ladder(self, uses, share, has_key, level):
        assert mastery_for(uses, share, NOW, has_key, NOW).level == level

    def test_seen(self):
        assert mastery_for(0, 0, None, True, NOW, seen=True).level == "seen"

    def test_decay_one_rung_after_grace_never_below_tried(self):
        fluent_last_used = NOW - 31 * DAY
        assert mastery_for(8, 1, fluent_last_used, True, NOW).level == "habitual"
        assert mastery_for(8, 1, NOW - 500 * DAY, True, NOW).level == "tried"
        assert mastery_for(8, 1, NOW - 29 * DAY, True, NOW).level == "fluent"

    def test_decay_is_configurable(self):
        assert mastery_for(8, 1, NOW - 10 * DAY, True, NOW, grace_days=5, step_days=100).level == "habitual"

    def test_hard_way(self):
        assert mastery_for(6, 0.1, NOW, True, NOW).hard_way
        assert not mastery_for(6, 0.9, NOW, True, NOW).hard_way
        assert not mastery_for(6, 0.0, NOW, False, NOW).hard_way


class TestCatalog:
    def test_every_palette_command_is_a_capability(self):
        from overcode.command_palette import COMMANDS
        ids = {c.id for c in J.build_catalog(_keymap())}
        assert {c.action for c in COMMANDS} <= ids

    def test_mapping_tables_name_real_actions(self):
        from overcode.command_palette import COMMANDS
        actions = {c.action for c in COMMANDS}
        assert set(J.RESIDUE_FOR_ACTION) <= actions
        assert set(J.EXTRA_ACTIONS) <= actions
        fields = set(Residue.__dataclass_fields__)
        assert set(J.RESIDUE_FOR_ACTION.values()) <= fields
        assert {c.residue for c in J.EXTRA_CAPABILITIES if c.residue} <= fields

    def test_competencies_name_real_things(self):
        """Every id a criterion counts is a capability or a TUI action; every try_action runs."""
        from overcode.tui import SupervisorTUI
        cap_ids = {c.id for c in J.build_catalog(_keymap())}
        src = inspect.getsource(J)
        block = src[src.index("COMPETENCIES = ("):src.index("# ── the journey")]
        named = set()
        for args in re.findall(r"s\.uses\(([^)]*)\)", block):
            named |= set(re.findall(r'"([a-z_]+)"', args))
        named |= set(J.APPROVE)
        unknown = {n for n in named if n not in cap_ids and not hasattr(SupervisorTUI, f"action_{n}")}
        assert unknown == set()
        for c in COMPETENCIES:
            if c.try_action:
                assert hasattr(SupervisorTUI, f"action_{c.try_action}"), c.try_action
        ids = {c.id for c in COMPETENCIES}
        for c in COMPETENCIES:
            assert set(c.prereqs) <= ids, c.id
        assert len(ids) == len(COMPETENCIES)

    def test_untracked_capability_stays_honest(self):
        j = _journey()
        cap = next(c for c in j.catalog if not c.tracked)
        assert j.to_dict()["capabilities"][cap.id]["tracked"] is False


class TestCurriculum:
    def test_empty_log_has_a_frontier_and_nothing_earned(self):
        j = _journey()
        basics = next(t for t in j.tracks if t.id == "basics")
        assert basics.level == 0 and basics.frontier
        assert any(c.id == "b_send" for c in basics.locked)  # needs b_navigate

    def test_earning_unlocks_the_next_step(self):
        j = _journey(("focus_next_session", "key", 3))
        basics = next(t for t in j.tracks if t.id == "basics")
        assert "b_navigate" in j.earned_ids
        assert any(c.id == "b_send" for c in basics.frontier + basics.upcoming)

    def test_competency_can_be_earned_before_its_prereq(self):
        j = _journey(("open_column_config", "key", 1))
        assert "o_columns" in j.earned_ids and "o_detail" not in j.earned_ids

    def test_residue_counts(self):
        j = _journey(residue=Residue(child_of_agent=2, live_agents=6, standing_orders=1, agents=3))
        assert {"x_children", "a_fleet", "f_new"} <= j.earned_ids
        assert j.mastery["focus_standing_orders"].level == "tried"

    def test_agent_driven_use_does_not_count(self):
        j = _journey(("toggle_timeline", "agent", 10))
        assert "o_timeline" not in j.earned_ids

    def test_palette_counts_via_the_palette_key_action(self):
        recs = _actions(("command_palette", "key", 1), ("toggle_timeline", "palette", 1))
        j = compute_journey(summarize(recs), Residue(), _keymap(), now_ms=NOW)
        assert "b_palette" in j.earned_ids

    def test_hard_way_lists_keyed_actions_done_slowly(self):
        j = _journey(("toggle_timeline", "palette", 5), ("toggle_timeline", "click", 5))
        assert "toggle_timeline" in j.hard_way

    def test_achievements(self):
        j = _journey(("toggle_timeline", "key", 1), ("toggle_timeline", "palette", 1),
                     ("toggle_timeline", "click", 1))
        earned = {c.id for c, e in j.achievements if e}
        assert "a_polyglot" in earned

    def test_core_only_counts_toward_the_level(self):
        j = _journey(("cli:view columns", "cli", 1))
        orch = next(t for t in j.tracks if t.id == "orchestration")
        assert "x_scripting" in j.earned_ids and orch.level == 0


class TestOutput:
    def test_render_and_dict(self):
        j = _journey(("focus_next_session", "key", 3), ("toggle_timeline", "palette", 5))
        text = render_journey(j)
        assert "Basics" in text and "✓ Move between agents" in text and "The hard way" in text
        d = j.to_dict()
        assert d["tracks"][0]["id"] == "basics" and d["hard_way"][0]["id"] == "toggle_timeline"
        assert d["capabilities"]["toggle_timeline"]["level"] in ("habitual", "fluent")


class TestJourneyPanelPilot:
    @pytest.mark.asyncio
    async def test_u_opens_it_and_t_tries_the_next_step(self, tmp_path, monkeypatch):
        from overcode.tui import SupervisorTUI
        from overcode.journey import Journey
        monkeypatch.setattr("overcode.journey.gather_residue", lambda: Residue())
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            for _ in range(20):
                await pilot.pause(0.05)
                if app.query_one("#journey-panel").has_class("visible"):
                    break
            panel = app.query_one("#journey-panel")
            assert panel.has_class("visible")
            assert isinstance(panel._journey, Journey)
            rendered = panel.render().plain
            assert "Basics" in rendered and "Capabilities" in rendered
            # The first track with a next step is Basics; its first try_action is focus_next_session.
            calls = []
            app.action_focus_next_session = lambda: calls.append(1)
            await pilot.press("t")
            await pilot.pause()
            assert not panel.has_class("visible")
            assert calls == [1]

    @pytest.mark.asyncio
    async def test_esc_closes(self, tmp_path, monkeypatch):
        from overcode.tui import SupervisorTUI
        monkeypatch.setattr("overcode.journey.gather_residue", lambda: Residue())
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            app.action_open_journey()
            for _ in range(20):
                await pilot.pause(0.05)
                if app.query_one("#journey-panel").has_class("visible"):
                    break
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query_one("#journey-panel").has_class("visible")
