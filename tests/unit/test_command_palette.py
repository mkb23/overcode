"""Tests for the command palette (#482): registry, matcher, and the widget
driven through the real TUI."""

from unittest.mock import patch

import pytest
from rich.cells import cell_len

from overcode.command_palette import (
    CATEGORIES, COMMANDS, MODE_SWITCHES, StateView, _baseline_state, _cycle,
    fuzzy_match, key_label, keys_by_action, rank_commands,
)


def _keymap():
    from overcode.tui import SupervisorTUI
    return keys_by_action(SupervisorTUI.BINDINGS)


def _titles(query):
    return [m.command.title for m in rank_commands(COMMANDS, query, _keymap())]


class TestRegistry:

    def test_every_bound_action_is_in_the_palette(self):
        from overcode.tui import SupervisorTUI
        bound = {b[1] for b in SupervisorTUI.BINDINGS} - {"command_palette"}
        assert bound - {c.action for c in COMMANDS} == set()

    def test_every_palette_action_exists(self):
        from overcode.tui import SupervisorTUI
        missing = [c.action for c in COMMANDS if not hasattr(SupervisorTUI, f"action_{c.action}")]
        assert missing == []

    def test_no_action_listed_twice(self):
        actions = [c.action for c in COMMANDS]
        assert len(actions) == len(set(actions))

    def test_categories_are_known_and_used(self):
        used = {c.category for c in COMMANDS}
        assert used == set(CATEGORIES)

    def test_palette_offers_unbound_actions(self):
        """The palette is the only way to reach cycle_notifications."""
        assert "cycle_notifications" not in _keymap()
        assert "cycle_notifications" in {c.action for c in COMMANDS}

    def test_mode_switches_are_commands(self):
        assert set(MODE_SWITCHES) <= {c.action for c in COMMANDS}

    def test_every_full_cycle_fits_the_palette(self):
        """At full width, each stateful row shows its whole cycle rather than
        falling back to "3 of 6"."""
        from overcode.tui_widgets.command_palette import MAX_WIDTH, _states
        keymap = _keymap()
        inner = MAX_WIDTH - 4
        title_w = max(cell_len(c.title) for c in COMMANDS)
        key_w = max(cell_len(" ".join(k)) for k in keymap.values())
        states_w = inner - 1 - title_w - 2 - key_w - 1
        for c in COMMANDS:
            if c.state is None:
                continue
            options = {
                "cycle_summary_content": ("short", "context", "orders", "note", "heartbeat", "command"),
            }.get(c.action)
            if options is None:
                continue
            text = _states(StateView(options, 0), states_w)
            assert " 1 of " not in text.plain, c.action


class TestKeyLabels:

    def test_labels(self):
        assert key_label("ctrl+p") == "^P"
        assert key_label("slash") == "/"
        assert key_label("left_square_bracket") == "["
        assert key_label("S") == "S"

    def test_keys_by_action_keeps_binding_order(self):
        keymap = _keymap()
        assert keymap["focus_next_session"] == ["j", "↓"]
        assert keymap["toggle_help"] == ["h", "?"]
        assert keymap["command_palette"] == ["/"]
        assert keymap["jump_to_agent"] == ["^P"]


class TestFuzzyMatch:

    def test_substring(self):
        score, pos = fuzzy_match("mary", "Summary detail")
        assert pos == (3, 4, 5, 6)

    def test_word_prefixes(self):
        assert fuzzy_match("sd", "Summary detail")[1] == (0, 8)
        assert fuzzy_match("sumdet", "Summary detail")[1] == (0, 1, 2, 8, 9, 10)

    def test_scattered_letters_do_not_match(self):
        assert fuzzy_match("sum", "Stop supervisor daemon") is None
        assert fuzzy_match("xyz", "Summary detail") is None

    def test_start_beats_middle(self):
        assert fuzzy_match("sum", "Summary detail")[0] > fuzzy_match("sum", "AI summarizer")[0]

    def test_empty_query(self):
        assert fuzzy_match("", "anything") == (0, ())


class TestRank:

    def test_empty_query_lists_everything_in_order(self):
        assert _titles("") == [c.title for c in COMMANDS]

    def test_query_ranks_title_start_first(self):
        assert _titles("sum")[:2] == ["Summary detail", "Summary content"]

    def test_exact_key_ranks_first_case_sensitively(self):
        assert _titles("S")[0] == "Sort agents"
        assert _titles("s")[0] == "Summary detail"

    def test_symbol_keys_find_their_command(self):
        assert _titles("$")[0] == "Cost units"
        assert _titles("<")[0] == "Timeline scope"

    def test_ctrl_key_any_case(self):
        assert _titles("^n")[0] == "Rename agent…"
        assert _titles("^N")[0] == "Rename agent…"

    def test_keywords_match_but_are_not_highlighted(self):
        matches = rank_commands(COMMANDS, "joules", _keymap())
        assert matches[0].command.action == "toggle_cost_display"
        assert matches[0].positions == ()

    def test_all_words_must_match(self):
        assert _titles("preview full") == ["Fullscreen preview"]
        assert _titles("preview zzz") == []


class TestStates:

    def test_cycle_unknown_value(self):
        assert _cycle(("a", "b"), "c").current is None
        assert _cycle(("a", "b"), "b").current_label == "b"

    @pytest.mark.parametrize("minutes,label", [(0, "now"), (15, "-15m"), (60, "-1h"), (75, "-1h15m")])
    def test_baseline_labels(self, minutes, label):
        class App:
            baseline_minutes = minutes
        assert _baseline_state(App()).current_label == label


# ---------------------------------------------------------------------------
# Through the real TUI
# ---------------------------------------------------------------------------

def _sessions():
    from overcode.session_manager import Session
    specs = [("alpha", "repo-a", "main", ["ui"]), ("bravo", "repo-b", "main", []),
             ("charlie", "repo-c", "feat/x", ["ui", "api"])]
    return [Session(id=f"id-{n}", name=n, tmux_session="test", tmux_window=f"w{i}",
                    command=["claude"], start_directory="/tmp",
                    start_time="2026-09-24T10:00:00", repo_name=r, branch=b, tags=t)
            for i, (n, r, b, t) in enumerate(specs)]


@pytest.fixture
def tui(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    from overcode.tui import SupervisorTUI
    with patch.object(SupervisorTUI, "_ensure_monitor_daemon", lambda self: None), \
         patch("overcode.launcher.AgentLauncher.list_sessions", lambda self: _sessions()):
        yield SupervisorTUI(tmux_session="test")


async def _ready(pilot):
    app = pilot.app
    for _ in range(50):
        if len(app._get_widgets_in_session_order()) == 3:
            break
        await pilot.pause(0.05)
    await pilot.pause(0.1)


def _palette(app):
    from overcode.tui_widgets import CommandPalette
    return app.query_one("#command-palette", CommandPalette)


@pytest.mark.asyncio
class TestPaletteInTUI:

    async def test_slash_opens_commands_and_esc_restores_focus(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            before = tui.focused
            await pilot.press("slash")
            palette = _palette(tui)
            assert palette.has_class("visible") and palette.mode == "commands"
            assert tui.focused is palette
            await pilot.press("escape")
            await pilot.pause()
            assert not palette.has_class("visible")
            assert tui.focused is before

    async def test_enter_runs_and_closes(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            level = tui.summary_level_index
            await pilot.press("slash", *"sumdet", "enter")
            await pilot.pause()
            assert tui.summary_level_index == (level + 1) % len(tui.SUMMARY_LEVELS)
            assert not _palette(tui).has_class("visible")
            assert tui._prefs.recent_commands[0] == "cycle_summary"

    async def test_tab_runs_and_stays_open(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            level = tui.summary_level_index
            await pilot.press("slash", *"sumdet", "tab", "tab")
            await pilot.pause()
            palette = _palette(tui)
            assert tui.summary_level_index == (level + 2) % len(tui.SUMMARY_LEVELS)
            assert palette.has_class("visible") and tui.focused is palette
            # The state column shows the new level lit
            state = palette.selected_row.match.command.state(tui)
            assert state.current == tui.summary_level_index

    async def test_tab_on_agent_command_acts_on_focused_agent(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("j")  # focus bravo
            await pilot.pause()
            session = tui._get_focused_widget().session
            assert session.name == "bravo"
            with patch.object(tui.session_manager, "update_session") as update:
                await pilot.press("slash", *"enhanced", "tab")
                await pilot.pause()
            update.assert_called_once_with("id-bravo", enhanced_context_enabled=True)
            assert _palette(tui).has_class("visible")
            state = _palette(tui).selected_row.match.command.state(tui)
            assert (state.current_label, state.note) == ("on", "bravo")

    async def test_tab_on_one_shot_command_closes(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"help", "tab")
            await pilot.pause()
            assert not _palette(tui).has_class("visible")
            assert tui.query_one("#help-overlay").has_class("visible")

    async def test_ctrl_p_jumps_to_agent(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("ctrl+p")
            assert _palette(tui).mode == "agents"
            await pilot.press(*"char", "enter")
            await pilot.pause()
            assert tui._get_focused_widget().session.name == "charlie"

    async def test_gt_switches_to_commands_and_backspace_back(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("ctrl+p", "greater_than_sign")
            palette = _palette(tui)
            assert palette.mode == "commands" and palette.text == ""
            await pilot.press("backspace")
            assert palette.mode == "agents"

    async def test_jump_command_switches_list_in_place(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"jump to", "enter")
            palette = _palette(tui)
            assert palette.has_class("visible") and palette.mode == "agents"

    async def test_tag_filter_and_clear(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("T")
            assert _palette(tui).mode == "tags"
            await pilot.press(*"api", "enter")
            await pilot.pause()
            assert tui.tag_filter == "api"
            await pilot.press("T")
            assert _palette(tui).selected_row.cand.name == "(clear filter)"
            await pilot.press("enter")
            await pilot.pause()
            assert tui.tag_filter is None

    async def test_recent_section_leads_after_use(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"timeline scope", "enter")
            await pilot.pause()
            await pilot.press("slash")
            palette = _palette(tui)
            assert palette._rows[0].header == "Recent"
            assert palette.selected_row.match.command.action == "cycle_timeline_hours"

    async def test_key_query_lights_the_key(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", "dollar_sign")
            row = _palette(tui).selected_row
            assert row.match.command.action == "toggle_cost_display" and row.match.by_key

    async def test_fits_a_short_terminal(self, tui):
        async with tui.run_test(size=(80, 16)) as pilot:
            await _ready(pilot)
            await pilot.press("slash")
            await pilot.pause()
            palette = _palette(tui)
            region = palette.region
            assert region.y + region.height <= 16
            assert region.x >= 0 and region.x + region.width <= 80
            # Scrolling to the end keeps the selection on screen
            await pilot.press("end")
            assert palette._scroll <= palette._items[palette.selected_index] < (
                palette._scroll + palette._list_height)

    async def test_agent_list_is_only_as_tall_as_the_agents(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("ctrl+p")
            await pilot.pause()
            palette = _palette(tui)
            assert palette._list_height == 3
            await pilot.press("greater_than_sign")
            await pilot.pause()
            assert palette._list_height > 3

    async def test_every_state_renders_on_a_live_app(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            for c in COMMANDS:
                if c.state is None:
                    continue
                state = c.state(tui)
                assert isinstance(state, StateView), c.action
                if not c.agent:
                    assert state.current is not None, c.action
            await pilot.press("slash")
            # Render every page of the list without error
            palette = _palette(tui)
            for _ in range(len(palette._items)):
                palette.render()
                await pilot.press("down")


class TestFooter:
    """The footer leads with `/` and keeps to the keys a newcomer needs."""

    def _footer(self, compact=False, toggle=None, mode="agents"):
        from types import SimpleNamespace
        from overcode.tui import SupervisorTUI
        app = SimpleNamespace(compact=compact, tui_mode=mode)
        with patch("overcode.config.get_tmux_toggle_key", return_value=toggle):
            return SupervisorTUI._build_footer_text(app).plain

    def test_leads_with_the_palette(self):
        text = self._footer()
        assert text.startswith(" / ") and "Commands" in text
        for part in ("n New agent", "j/k Next/prev", "^P Jump to agent", "? Help", "q Quit"):
            assert part in text
        assert "Switch pane" not in text

    def test_split_mode_names_the_configured_toggle_key(self):
        assert "Tab Switch pane" in self._footer(compact=True)
        assert "Ctrl+Space Switch pane" in self._footer(compact=True, toggle="C-Space")

    def test_jobs_view(self):
        text = self._footer(mode="jobs")
        assert text.startswith(" / ") and "J Agents" in text and "New agent" not in text
