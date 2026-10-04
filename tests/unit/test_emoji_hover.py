"""Skill profile emoji (PRF) and hover popups naming a row's emoji."""

import pytest
from typer.testing import CliRunner

from overcode import skill_library as sl
from overcode.status_constants import BADGE_KINDS, BADGE_MEANINGS, StatusBadge, StatusDetail
from overcode.summary_columns import (
    COLUMN_HOVER, COLUMNS_BY_ID, hover_allowed_tools, hover_loaded_skills,
    hover_skill_profile, hover_status_detail, render_skill_profile,
    render_skill_profile_plain,
)
from tests.unit.test_command_palette import _ready, tui  # noqa: F401 (fixture)
from tests.unit.test_summary_columns import _make_ctx, _make_session


def _ctx(**session_fields):
    extra = {k: session_fields.pop(k) for k in ("emoji_free", "status_detail") if k in session_fields}
    return _make_ctx(session=_make_session(**session_fields), **extra)


class TestProfileEmojiRegister:

    def test_default_until_set_then_cleared(self):
        sl.save_profile("science", ["shirka"])
        assert sl.profile_emoji("science") == sl.PROFILE_EMOJI_DEFAULT
        sl.set_profile_emoji("science", "🔬")
        assert sl.profile_emoji("science") == "🔬"
        assert sl.get_profile_emojis() == {"science": "🔬"}
        sl.set_profile_emoji("science", None)
        assert sl.get_profile_emojis() == {}

    def test_deleting_a_profile_drops_its_emoji(self):
        sl.save_profile("science", [])
        sl.save_profile("writing", [])
        sl.set_profile_emoji("science", "🔬")
        sl.set_profile_emoji("writing", "✍️")
        sl.delete_profile("science")
        assert sl.get_profile_emojis() == {"writing": "✍️"}

    def test_cli_sets_and_lists_it(self):
        from overcode.cli import app
        sl.save_profile("science", ["shirka"])
        runner = CliRunner()
        result = runner.invoke(app, ["skills", "profile", "emoji", "science", "🔬"])
        assert result.exit_code == 0, result.output
        assert sl.profile_emoji("science") == "🔬"
        listing = runner.invoke(app, ["skills", "profile", "list"]).output
        assert "🔬" in listing and "science" in listing
        result = runner.invoke(app, ["skills", "profile", "emoji", "science"])
        assert "default" in result.output
        assert sl.get_profile_emojis() == {}

    def test_cli_rejects_an_unknown_profile(self):
        from overcode.cli import app
        result = CliRunner().invoke(app, ["skills", "profile", "emoji", "nope", "🔬"])
        assert result.exit_code != 0
        assert sl.get_profile_emojis() == {}


class TestPrfColumn:

    def test_shows_the_profile_emoji(self):
        sl.save_profile("science", ["shirka"])
        sl.set_profile_emoji("science", "🔬")
        [(text, _)] = render_skill_profile(_ctx(skill_profile="science"))
        assert text == " 🔬"

    def test_unregistered_profile_gets_the_default(self):
        [(text, _)] = render_skill_profile(_ctx(skill_profile="writing"))
        assert text == f" {sl.PROFILE_EMOJI_DEFAULT}"

    def test_emoji_free_terminals_get_the_name(self):
        [(text, _)] = render_skill_profile(_ctx(skill_profile="writing", emoji_free=True))
        assert text == " writing"

    def test_plain_has_both(self):
        sl.set_profile_emoji("science", "🔬")
        assert render_skill_profile_plain(_ctx(skill_profile="science")) == "🔬 science"

    def test_no_profile_renders_nothing(self):
        assert render_skill_profile(_ctx(skill_profile=None)) is None


class TestHoverText:

    def test_registry_names_real_columns(self):
        assert set(COLUMN_HOVER) <= set(COLUMNS_BY_ID)
        assert COLUMNS_BY_ID["skill_profile"].hover is hover_skill_profile

    def test_every_badge_has_a_meaning(self):
        assert set(BADGE_MEANINGS) == set(BADGE_KINDS)

    def test_loaded_skills_are_named(self):
        tip = hover_loaded_skills(_ctx(loaded_skills=["overcode", "mystery"])).plain
        assert "🐙  overcode" in tip and "🧩  mystery" in tip

    def test_profile_popup_lists_its_skills_and_how_to_set_an_emoji(self):
        sl.save_profile("science", ["overcode", "mystery"])
        tip = hover_skill_profile(_ctx(skill_profile="science")).plain
        assert "science" in tip and "🐙  overcode" in tip and "🧩  mystery" in tip
        assert "overcode skills profile emoji science" in tip
        sl.set_profile_emoji("science", "🔬")
        tip = hover_skill_profile(_ctx(skill_profile="science")).plain
        assert "🔬 science" in tip and "skills profile emoji" not in tip

    def test_allowed_tools_are_named(self):
        tip = hover_allowed_tools(_ctx(allowed_tools="Read, Bash")).plain
        assert "Read" in tip and "Bash" in tip

    def test_status_badges_are_explained_with_count_and_eta(self):
        detail = StatusDetail("color_yellow", [
            StatusBadge("subagent", count=2),
            StatusBadge("schedule_wakeup", eta_seconds=300),
        ])
        tip = hover_status_detail(_ctx(status_detail=detail)).plain
        assert "👥  subagent still running ×2" in tip
        assert "wakeup scheduled, in 5" in tip

    def test_nothing_to_explain_is_no_popup(self):
        assert hover_loaded_skills(_ctx(loaded_skills=[])) is None
        assert hover_skill_profile(_ctx(skill_profile=None)) is None


@pytest.fixture
def agents_with_tools(monkeypatch):
    """Agents that carry allowed tools, patched in before the TUI is built
    (it loads its agents in __init__)."""
    from tests.unit import test_command_palette as tcp
    plain = tcp._sessions

    def with_tools():
        sessions = plain()
        # Different per agent, or the column hides as uniform
        for s, tools in zip(sessions, ("Read,Bash", "Read", None)):
            s.allowed_tools = tools
        return sessions

    monkeypatch.setattr(tcp, "_sessions", with_tools)


@pytest.fixture
def tools_tui(agents_with_tools, tui):  # noqa: F811 (fixture)
    return tui


@pytest.mark.asyncio
class TestRowHoverInTUI:

    async def test_hovering_an_emoji_cell_names_its_emoji(self, tools_tui):
        from overcode.tui_widgets import SessionSummary
        app = tools_tui
        async with app.run_test(size=(220, 40)) as pilot:
            await _ready(pilot)
            row = next(iter(app.query(SessionSummary)))
            ids = [c.id for c in COLUMNS_BY_ID.values() if row.column_visible(c)]
            assert "allowed_tools" in ids
            x = 0
            for cid, w in zip(ids, app.column_widths):
                if cid == "allowed_tools":
                    assert w > 0
                    break
                x += w
            # +1 for the row's left padding
            await pilot.hover(row, offset=(x + 2, 0))
            assert row.tooltip is not None
            assert "Allowed tools" in row.tooltip.plain and "Bash" in row.tooltip.plain
            # A column with no popup clears it
            await pilot.hover(row, offset=(2, 0))
            assert row.tooltip is None
