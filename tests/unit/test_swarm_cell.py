"""The SubAgents cell (👥): a parent's delegated work as the engine published it.

docs/design/agent-observability-0.6.1.md §4.3. The TUI and ``overcode
list`` render ``swarm_rollup``; they compute nothing. Merged in 0.6.1 from
DLG (``swarm``) and SUB (``subagent_count``): the hook rollup when there is
one, else the transcript-file count of live subagents.
"""

import pytest

pytestmark = pytest.mark.unit


def _rollup(**counts):
    base = {"total": 0, "running": 0, "waiting": 0, "idle": 0, "ok": 0, "failed": 0,
            "workflows": 0, "workflows_running": 0}
    base.update(counts)
    return base


def _ctx(**kw):
    from tests.unit.test_summary_columns import _make_ctx
    return _make_ctx(**kw)


class TestCell:
    def _text(self, **kw):
        from overcode.columns.render_performance import render_subagents
        return render_subagents(_ctx(**kw))[0][0]

    def test_each_state_s_count_non_zero_only_and_no_total(self):
        rollup = _rollup(total=43, running=15, waiting=1, ok=26, failed=1, workflows=1,
                         workflows_running=1)
        assert self._text(swarm_rollup=rollup) == " 👥 ▶15 🟠1 ✅26 ❌1 🧩1"
        assert self._text(swarm_rollup=_rollup(total=2, running=2)) == " 👥 ▶2"
        assert self._text(swarm_rollup=_rollup(total=3, ok=3)) == " 👥 ✅3"

    def test_members_on_their_own_background_work_show_their_light(self):
        """🟡 as on their rows (waiting on a shell of their own), not ▶."""
        rollup = _rollup(total=17, running=1, yellow=2, ok=14)
        assert self._text(swarm_rollup=rollup) == " 👥 ▶1 🟡2 ✅14"
        assert self._text(swarm_rollup=_rollup(total=2, yellow=2)) == " 👥 🟡2"

    def test_a_sister_s_rollup_without_yellow_still_draws(self):
        rollup = {"total": 2, "running": 2, "ok": 0}
        assert self._text(swarm_rollup=rollup) == " 👥 ▶2"

    def test_idle_teammates_show_so_a_listening_team_is_not_blank(self):
        assert self._text(swarm_rollup=_rollup(total=2, idle=2)) == " 👥 ⚪2"

    def test_the_fold_marker_in_the_tui(self):
        rollup = _rollup(total=1, running=1)
        assert self._text(swarm_rollup=rollup, is_list_mode=True) == "▸👥 ▶1"
        assert self._text(swarm_rollup=rollup, is_list_mode=True,
                          swarm_expanded=True) == "▾👥 ▶1"

    def test_ascii_mode(self):
        rollup = _rollup(total=4, running=1, waiting=1, idle=1, ok=1, workflows=2)
        assert self._text(swarm_rollup=rollup, emoji_free=True) == " Sa >1 [A]1 [.]1 ok1 Wk2"

    def test_without_a_rollup_the_live_subagent_count(self):
        """No hooks (or a sister too old to publish counts): the transcript
        files still say how many run; nothing to expand, so no ▸."""
        assert self._text(live_subagent_count=2, is_list_mode=True) == " 👥 ▶2"
        assert self._text(live_subagent_count=2, emoji_free=True) == " Sa >2"

    def test_the_rollup_wins_over_the_file_count(self):
        rollup = _rollup(total=3, ok=3)
        assert self._text(swarm_rollup=rollup, live_subagent_count=2) == " 👥 ✅3"

    def test_a_rollup_with_no_counts_keeps_the_placeholder_s_shape(self):
        assert self._text(swarm_rollup=_rollup(total=1), is_list_mode=True) == "▸👥  -"

    @pytest.mark.parametrize("emoji_free", [False, True])
    def test_the_placeholder_has_a_short_value_s_shape(self, emoji_free):
        from rich.cells import cell_len
        empty = self._text(swarm_rollup=None, emoji_free=emoji_free)
        assert empty.endswith(" -")
        for one in (self._text(swarm_rollup=_rollup(total=1, running=1), emoji_free=emoji_free),
                    self._text(live_subagent_count=1, emoji_free=emoji_free)):
            assert cell_len(empty) == cell_len(one)

    def test_hidden_until_some_agent_delegates(self):
        from overcode.columns.context import FleetFlags
        from overcode.summary_columns import COLUMNS_BY_ID
        col = COLUMNS_BY_ID["subagents"]
        assert not col.visible(_ctx())
        assert col.visible(_ctx(fleet=FleetFlags(any_has_swarm=True)))
        assert col.visible(_ctx(fleet=FleetFlags(any_has_live_subagents=True)))

    def test_shown_at_every_level_left_of_the_name(self):
        """→ expands from it, so it is where DLG was: every level, before the name."""
        from overcode.summary_columns import ALL, COLUMNS_BY_ID, SUMMARY_COLUMNS
        assert COLUMNS_BY_ID["subagents"].detail_levels == ALL
        ids = [c.id for c in SUMMARY_COLUMNS]
        assert ids.index("subagents") < ids.index("agent_name")
        assert COLUMNS_BY_ID["subagents"].name == "SubAgents"

    def test_hover_names_each_count_and_the_total(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        rollup = _rollup(total=5, running=1, waiting=1, idle=1, ok=1, failed=1, workflows=1,
                         workflows_running=1)
        hover = COLUMNS_BY_ID["subagents"].hover
        text = hover(_ctx(swarm_rollup=rollup)).plain
        for words in ("5 agents delegated", "1 working", "1 waiting for approval",
                      "1 idle teammates", "1 finished", "1 failed", "1 workflow run, 1 running"):
            assert words in text
        assert "2 running" in hover(_ctx(live_subagent_count=2)).plain
        assert hover(_ctx()) is None

    def test_sorts_work_in_flight_first(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        key = COLUMNS_BY_ID["subagents"].sort_key
        assert key(_ctx()) is None
        busy = key(_ctx(swarm_rollup=_rollup(total=3, running=2, ok=1)))
        done = key(_ctx(swarm_rollup=_rollup(total=30, ok=30)))
        assert busy > done
        assert key(_ctx(live_subagent_count=2)) == (2, 2)

    def test_the_tui_row_takes_it_from_the_view(self):
        from overcode.tui_widgets.session_summary import SessionSummary
        row = SessionSummary.__new__(SessionSummary)
        row._apply_results({"swarm_rollup": _rollup(total=1, running=1)})
        assert row.swarm_rollup == _rollup(total=1, running=1)
        row._apply_results({"swarm_rollup": None})
        assert row.swarm_rollup is None


class TestEmojiFreeGlyphs:
    def test_the_cell_s_ascii_glyphs_are_distinct_and_the_workflow_s_is_its_own(self):
        from overcode.columns.render_performance import SUBAGENT_SEGMENTS
        from overcode.status_constants import EMOJI_ASCII, WORKFLOW_ASCII
        glyphs = [EMOJI_ASCII[g].strip() for _, g in SUBAGENT_SEGMENTS] + [WORKFLOW_ASCII]
        assert len(set(glyphs)) == len(glyphs)
        assert WORKFLOW_ASCII not in {v.strip() for v in EMOJI_ASCII.values()}  # "Wf" is WebFetch's

    def test_one_glyph_one_ascii_form_in_the_status_detail_column(self):
        """The legacy wakeup countdown and the wakeup badge both draw ⏰."""
        from overcode.status_constants import BADGE_KINDS, EMOJI_ASCII
        assert EMOJI_ASCII["⏰"] == BADGE_KINDS["schedule_wakeup"][2]


class TestOldColumnIds:
    """DLG (swarm) and SUB (subagent_count) were merged: what named them keeps working."""

    def test_aliases(self):
        from overcode.summary_columns import COLUMNS_BY_ID, canonical_column_id
        assert canonical_column_id("swarm") == "subagents"
        assert canonical_column_id("subagent_count") == "subagents"
        assert canonical_column_id("cost") == "cost"
        assert "swarm" not in COLUMNS_BY_ID and "subagent_count" not in COLUMNS_BY_ID

    @pytest.mark.parametrize("overrides, expected", [
        ({"swarm": False, "cost": True}, {"subagents": False, "cost": True}),
        ({"subagent_count": True}, {"subagents": True}),
        ({"swarm": False, "subagent_count": True}, {"subagents": True}),  # shown wins
        ({"swarm": True, "subagents": False}, {"subagents": False}),  # the new id stands
        ({"cost": True}, {"cost": True}),
    ])
    def test_overrides_migrate(self, overrides, expected):
        from overcode.summary_columns import migrate_column_overrides
        assert migrate_column_overrides(overrides) == expected

    def test_an_old_prefs_file_loads_with_the_new_id(self, tmp_path, monkeypatch):
        import json
        from overcode import settings
        prefs = tmp_path / "tui_preferences.json"
        prefs.write_text(json.dumps({
            "column_config": {"high": {"subagent_count": False, "uptime": True},
                              "low": {"swarm": True}},
            "sort_mode": "col:swarm",
            "some_future_key": 1,
        }))
        monkeypatch.setattr(settings, "get_tui_preferences_path", lambda session: prefs)
        loaded = settings.TUIPreferences.load("agents")
        assert loaded.column_config == {"high": {"subagents": False, "uptime": True},
                                        "low": {"subagents": True}}
        assert loaded.sort_mode == "col:subagents"

    def test_view_commands_accept_the_old_ids(self):
        from overcode.tui_actions.view_control import ViewControlMixin
        for name in ("swarm", "subagent_count", "SUBA", "subagents", "SubAgents"):
            assert ViewControlMixin._resolve_column(None, name).id == "subagents"


class TestRemovedResultColumn:
    """RES (task_result) became the summary's result mode (0.6.1): what named it keeps loading."""

    @pytest.mark.parametrize("overrides, expected", [
        ({"task_result": True, "cost": False}, {"cost": False}),
        ({"task_result": False, "swarm": True}, {"subagents": True}),
    ])
    def test_overrides_drop_it(self, overrides, expected):
        from overcode.summary_columns import migrate_column_overrides
        assert migrate_column_overrides(overrides) == expected

    def test_an_old_prefs_file_loads_without_it(self, tmp_path, monkeypatch):
        import json
        from overcode import settings
        prefs = tmp_path / "tui_preferences.json"
        prefs.write_text(json.dumps({
            "column_config": {"full": {"task_result": False, "uptime": True}},
            "sort_mode": "col:task_result",
            "summary_content_mode": "result",
        }))
        monkeypatch.setattr(settings, "get_tui_preferences_path", lambda session: prefs)
        loaded = settings.TUIPreferences.load("agents")
        assert loaded.column_config == {"full": {"uptime": True}}
        assert loaded.sort_mode == settings.TUIPreferences().sort_mode
        assert loaded.summary_content_mode == "result"

    def test_view_commands_say_where_it_went(self):
        from overcode.tui_actions.view_control import ViewCommandError, ViewControlMixin
        with pytest.raises(ViewCommandError, match="result mode"):
            ViewControlMixin._resolve_column(None, "task_result")
