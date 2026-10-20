"""The name column's marks for automatic names (#542): dim default, "~" auto.

The marks take no width of their own: the NAME column is as wide whatever
an agent's name source, so nothing moves when a name is handed to the
namer. The layout matrix runs the same fleet at every width and detail.
"""

import pytest
from rich.text import Text

from overcode.columns.hover import hover_agent_name
from tests.tui_harness import ready, three_agents

pytestmark = pytest.mark.unit


def _name_cell(row):
    ids = [c.id for c in row.visible_columns()]
    return row.row_cells()[ids.index("agent_name")]


def _rows(app):
    return {w.session.name: w for w in app._get_widgets_in_session_order()}


async def test_marks_and_a_stable_width(make_tui):
    agents = three_agents()
    agents[0].name_source = "default"
    agents[1].name_source = "auto"
    app = make_tui(agents, size=(200, 30))
    async with app.run_test(size=(200, 30)) as pilot:
        await ready(pilot)
        rows = _rows(app)
        cells = {name: _name_cell(row) for name, row in rows.items()}
        assert cells["bravo"].plain.startswith("~bravo")
        assert not cells["alpha"].plain.startswith("~")
        assert not cells["charlie"].plain.startswith("~")
        assert len({c.cell_len for c in cells.values()}) == 1
        assert any("dim" in str(span.style) for span in cells["alpha"].spans)
        assert not any("dim" in str(span.style) for span in cells["charlie"].spans)
        widths = list(app.column_widths)

        # Handing a name to the namer changes nothing else on screen
        rows["charlie"].session.name_source = "auto"
        rows["charlie"]._overlay_key = None
        rows["charlie"].refresh()
        await pilot.pause()
        assert _name_cell(rows["charlie"]).plain.startswith("~charlie")
        assert list(app.column_widths) == widths


def test_hover_says_who_named_it_and_what_it_was_called():
    from types import SimpleNamespace
    session = SimpleNamespace(name="jwt-refresh", name_source="auto", name_forced=True,
                              previous_names=["overcode-main2", "wiki"], is_remote=False)
    text = hover_agent_name(SimpleNamespace(session=session))
    assert isinstance(text, Text)
    plain = text.plain
    assert "named by overcode" in plain
    assert "--force-name" in plain
    assert "was: wiki, overcode-main2" in plain
    session.name_source = "human"
    session.name_forced = False
    session.previous_names = []
    assert "never renames" in hover_agent_name(SimpleNamespace(session=session)).plain
    session.is_remote = True
    assert hover_agent_name(SimpleNamespace(session=session)) is None


def test_hover_names_the_title_claude_still_shows():
    from types import SimpleNamespace
    session = SimpleNamespace(name="limbic", name_source="human", name_forced=False,
                              previous_names=["limbic2"], is_remote=False)
    plain = hover_agent_name(SimpleNamespace(session=session, cli_title_lag="limbic2")).plain
    assert "Claude still shows 'limbic2'" in plain and "/rename" in plain
    plain = hover_agent_name(SimpleNamespace(session=session, cli_title_lag=None)).plain
    assert "Claude still shows" not in plain
