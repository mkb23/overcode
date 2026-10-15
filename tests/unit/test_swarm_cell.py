"""The 👥 cell (DLG): a parent's delegated work as the engine published it.

docs/design/agent-observability-0.6.1.md §4.3. The TUI and ``overcode
list`` render ``swarm_rollup``; they compute nothing.
"""

import pytest

pytestmark = pytest.mark.unit


def _rollup(**counts):
    base = {"total": 0, "running": 0, "waiting": 0, "idle": 0, "ok": 0, "failed": 0,
            "workflows": 0, "workflows_running": 0}
    base.update(counts)
    return base


class TestCell:
    def _text(self, **kw):
        from overcode.columns.render_performance import render_swarm
        from tests.unit.test_summary_columns import _make_ctx
        return render_swarm(_make_ctx(**kw))[0][0]

    def test_non_zero_segments_only(self):
        rollup = _rollup(total=43, running=15, waiting=1, ok=27, failed=1, workflows=1,
                         workflows_running=1)
        assert self._text(swarm_rollup=rollup) == " 👥 43 ▶15 🟠1 ✅27 ❌1 🧩1"
        assert self._text(swarm_rollup=_rollup(total=2, running=2)) == " 👥  2 ▶2"

    def test_ascii_mode(self):
        rollup = _rollup(total=3, running=1, waiting=1, ok=1, workflows=2)
        assert self._text(swarm_rollup=rollup, emoji_free=True) == " Sa  3 >1 [A]1 ok1 Wf2"

    @pytest.mark.parametrize("emoji_free", [False, True])
    def test_the_placeholder_has_a_one_digit_total_s_shape(self, emoji_free):
        from rich.cells import cell_len
        empty = self._text(swarm_rollup=None, emoji_free=emoji_free)
        one = self._text(swarm_rollup=_rollup(total=1, ok=1), emoji_free=emoji_free)
        assert empty.endswith(" -")
        assert cell_len(empty) == cell_len(one.rsplit(" ", 1)[0])
        two = self._text(swarm_rollup=_rollup(total=2), emoji_free=emoji_free)
        assert cell_len(empty) == cell_len(two)

    def test_hidden_until_some_agent_delegates(self):
        from overcode.columns.context import FleetFlags
        from overcode.summary_columns import COLUMNS_BY_ID
        from tests.unit.test_summary_columns import _make_ctx
        col = COLUMNS_BY_ID["swarm"]
        assert not col.visible(_make_ctx())
        assert col.visible(_make_ctx(fleet=FleetFlags(any_has_swarm=True)))

    def test_hover_names_each_count(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        from tests.unit.test_summary_columns import _make_ctx
        rollup = _rollup(total=5, running=1, waiting=1, idle=1, ok=1, failed=1, workflows=1,
                         workflows_running=1)
        text = COLUMNS_BY_ID["swarm"].hover(_make_ctx(swarm_rollup=rollup)).plain
        for words in ("5 agents delegated", "1 working", "1 waiting for approval",
                      "1 idle teammates", "1 finished", "1 failed", "1 workflow run, 1 running"):
            assert words in text
        assert COLUMNS_BY_ID["swarm"].hover(_make_ctx()) is None

    def test_the_tui_row_takes_it_from_the_view(self):
        from overcode.tui_widgets.session_summary import SessionSummary
        row = SessionSummary.__new__(SessionSummary)
        row._apply_results({"swarm_rollup": _rollup(total=1, running=1)})
        assert row.swarm_rollup == _rollup(total=1, running=1)
        row._apply_results({"swarm_rollup": None})
        assert row.swarm_rollup is None
