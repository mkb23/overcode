"""Tagging agents from the TUI (#538): "Add tag…" (#) and "Remove tag…" in
the palette, through the CLI's path (SessionManager.add_tags /
remove_tags), and the sort that groups agents by first tag. Synthetic
records only."""

from types import SimpleNamespace

import pytest

from overcode.command_palette import COMMANDS, MODE_SWITCHES, sort_choices
from overcode.session_manager import SessionManager
from overcode.tui_logic import (
    get_sort_mode_display_name, sort_descending, sort_sessions, sort_sessions_by_tag,
)
from overcode.tui_widgets.command_palette import typed_tags
from tests.tui_harness import palette as _palette, ready as _ready


# ── registry and keys ────────────────────────────────────────────────────

class TestRegistry:
    @pytest.mark.parametrize("action, title, mode", [
        ("add_tag", "Add tag…", "tag_add"), ("remove_tag", "Remove tag…", "tag_remove"),
    ])
    def test_palette_entries_act_on_the_agent_and_list_in_place(self, action, title, mode):
        entry = next(c for c in COMMANDS if c.action == action)
        assert (entry.title, entry.category, entry.agent) == (title, "Agent", True)
        assert MODE_SWITCHES[action] == mode

    def test_the_help_lists_both(self):
        from overcode.tui_widgets.help_overlay import HelpOverlay
        text = HelpOverlay().render_text(200).plain
        assert "Add tag…" in text and "Remove tag…" in text


class TestKeys:
    def test_hash_adds_a_tag_in_every_preset(self):
        from overcode.keymap import effective_keymap, list_presets
        for preset in list_presets():
            km = effective_keymap(preset=preset, use_config=False)
            assert km.action_for("number_sign") == "add_tag", preset
            assert km.keys_for("add_tag") == ["number_sign"], preset

    def test_remove_tag_is_palette_only(self):
        from overcode.keymap import effective_keymap, list_presets
        for preset in list_presets():
            assert effective_keymap(preset=preset, use_config=False).keys_for("remove_tag") == []


# ── pure parts ───────────────────────────────────────────────────────────

class TestTypedTags:
    def test_lower_cased_split_and_deduplicated(self):
        assert typed_tags("  Backend hot-path backend ") == ["backend", "hot-path"]

    def test_blank_is_none(self):
        assert typed_tags("   ") == []


def _s(name, tags=(), parent=None):
    return SimpleNamespace(id=f"id-{name}", name=name, tags=list(tags), parent_session_id=parent)


class TestSortByTag:
    def test_groups_by_first_tag_then_name_untagged_last(self):
        sessions = [_s("zed", ["api"]), _s("bob"), _s("amy", ["ui", "api"]),
                    _s("cat", ["api"]), _s("abe")]
        names = [s.name for s in sort_sessions_by_tag(sessions)]
        assert names == ["cat", "zed", "amy", "abe", "bob"]

    def test_reversed_flips_groups_untagged_still_last(self):
        sessions = [_s("zed", ["api"]), _s("bob"), _s("amy", ["ui"])]
        names = [s.name for s in sort_sessions(sessions, "by_tag", reverse=True)]
        assert names == ["amy", "zed", "bob"]

    def test_children_stay_under_their_parent(self):
        sessions = [_s("parent", ["zz"]), _s("kid", ["aa"], parent="id-parent"), _s("other", ["mm"])]
        names = [s.name for s in sort_sessions_by_tag(sessions)]
        assert names == ["other", "parent", "kid"]

    def test_direction_and_name(self):
        assert sort_descending("by_tag", False) is False
        assert sort_descending("by_tag", True) is True
        assert get_sort_mode_display_name("by_tag") == "By Tag"

    def test_the_sort_picker_offers_it(self):
        choices = {c.mode: c for c in sort_choices("by_tag", True)}
        tag = choices["by_tag"]
        assert tag.name == "Tag" and tag.active and tag.descending
        assert not choices["by_tree"].active


# ── through the TUI ──────────────────────────────────────────────────────

def _fleet():
    sm = SessionManager(skip_git_detection=True)
    made = {}
    for name, tags in (("alpha", ["ui"]), ("bravo", []), ("charlie", ["ui", "api"])):
        s = sm.create_session(name=name, tmux_session="test", tmux_window=f"{name}-w",
                              command=["claude"], start_directory="/tmp")
        if tags:
            sm.add_tags(s.id, tags)
        made[name] = s.id
    return sm, made


async def _highlight(app, pilot, name):
    for i, w in enumerate(app._get_widgets_in_session_order()):
        if w.session.name == name:
            app.focused_session_index = i
            w.focus()
            break
    await pilot.pause()
    assert app._get_focused_widget().session.name == name


async def _settle(app, pilot, sid, want):
    for _ in range(100):
        await pilot.pause(0.02)
        w = next((w for w in app._get_widgets_in_session_order() if w.session.id == sid), None)
        if w is not None and list(w.session.tags or []) == want:
            return
    raise AssertionError(f"row tags never became {want}")


@pytest.mark.asyncio
class TestTagFromTheTui:
    async def test_hash_offers_the_fleets_other_tags_and_adds_one(self, make_tui):
        sm, ids = _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            await _highlight(app, pilot, "alpha")
            await pilot.press("number_sign")
            pal = _palette(app)
            assert pal.has_class("visible") and pal.mode == "tag_add"
            assert "alpha" in str(pal.border_title)
            assert [c.name for c in pal._tag_add] == ["api"]  # ui is alpha's already
            await pilot.press("enter")
            await _settle(app, pilot, ids["alpha"], ["ui", "api"])
            # The highlight stays on the agent it tagged
            assert app._get_focused_widget().session.id == ids["alpha"]
        assert sm.get_session(ids["alpha"]).tags == ["ui", "api"]

    async def test_a_typed_new_tag_is_lower_cased(self, make_tui):
        sm, ids = _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            await _highlight(app, pilot, "bravo")
            await pilot.press("number_sign", *"Hot-Path")
            row = _palette(app).selected_row
            assert row.cand.name == "+ hot-path" and row.cand.repo == "new tag"
            from rich.cells import cell_len
            pal = _palette(app)
            assert all(cell_len(line) <= pal.size.width for line in pal.render().plain.split("\n"))
            await pilot.press("enter")
            await _settle(app, pilot, ids["bravo"], ["hot-path"])
        assert sm.get_session(ids["bravo"]).tags == ["hot-path"]

    async def test_typing_an_existing_tag_picks_it_rather_than_a_new_row(self, make_tui):
        _, _ = _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            await _highlight(app, pilot, "bravo")
            await pilot.press("number_sign", *"ui")
            pal = _palette(app)
            assert [r.cand.name for r in pal._rows] == ["ui"]

    async def test_remove_lists_the_agents_tags_from_the_palette(self, make_tui):
        sm, ids = _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            await _highlight(app, pilot, "charlie")
            await pilot.press("slash", *"remove tag", "enter")
            pal = _palette(app)
            assert pal.has_class("visible") and pal.mode == "tag_remove"
            assert [c.name for c in pal._tag_remove] == ["ui", "api"]
            await pilot.press(*"api", "enter")
            await _settle(app, pilot, ids["charlie"], ["ui"])
        assert sm.get_session(ids["charlie"]).tags == ["ui"]

    async def test_remove_on_an_untagged_agent_says_so(self, make_tui):
        _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            await _highlight(app, pilot, "bravo")
            notes = []
            app.notify = lambda msg, **kw: notes.append(msg)
            await pilot.press("slash", *"remove tag", "enter")
            await pilot.pause()
            assert not _palette(app).has_class("visible")
            assert any("has no tags" in n for n in notes)

    async def test_a_remote_agent_is_refused(self, make_tui):
        remote = SimpleNamespace(id="remote:h:a", name="a", is_remote=True, tags=[])
        app = make_tui(sessions=[])
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause()
            app._get_focused_widget = lambda: SimpleNamespace(session=remote)
            notes = []
            app.notify = lambda msg, **kw: notes.append(msg)
            app.action_add_tag()
            assert not _palette(app).has_class("visible")
            assert any("overcode tag a" in n for n in notes)

    async def test_sort_by_tag_from_the_picker(self, make_tui):
        _fleet()
        app = make_tui()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(pilot)
            app.set_sort_mode("by_tag")
            await pilot.pause()
            names = [w.session.name for w in app._get_widgets_in_session_order()]
            assert names == ["alpha", "charlie", "bravo"]  # both first-tagged ui; untagged last
