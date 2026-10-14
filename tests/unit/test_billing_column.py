"""The billing-code column, and the code's way from the daemon to the row.

The TUI is a view of the engine: the daemon publishes each agent's code in
its SessionDaemonState, the TUI's row overlays it on the session, and the
column renders it. Synthetic records only.
"""

from types import SimpleNamespace

from rich.cells import cell_len

from overcode.monitor_daemon_state import SessionDaemonState
from overcode.summary_columns import (
    COLUMNS_BY_ID,
    render_billing_code,
    render_billing_code_plain,
    resolve_column_visible,
)
from overcode.columns.render_usage import BILLING_CODE_W
from overcode.tui_engine import session_with_view


def _ctx(code, name="alpha"):
    return SimpleNamespace(session=SimpleNamespace(name=name, billing_code=code), bg="")


def _text(cells):
    return "".join(t for t, _ in cells)


class TestColumn:
    def test_placeholder_is_as_wide_as_a_value(self):
        widths = {cell_len(_text(render_billing_code(_ctx(code))))
                  for code in (None, "", "x", "client-a", "a-very-long-client-code-indeed")}
        assert widths == {BILLING_CODE_W + 1}

    def test_long_codes_are_cut_with_an_ellipsis(self):
        text = _text(render_billing_code(_ctx("a-very-long-client-code")))
        assert text.strip().endswith("…")

    def test_the_default_code_is_dim_a_chosen_one_is_not(self):
        (_, own), = render_billing_code(_ctx("alpha", name="alpha"))
        (_, chosen), = render_billing_code(_ctx("client-a", name="alpha"))
        assert own.startswith("dim") and not chosen.startswith("dim")

    def test_plain_text_for_overcode_list(self):
        assert render_billing_code_plain(_ctx("client-a")) == "client-a"
        assert render_billing_code_plain(_ctx(None)) is None

    def test_hidden_by_default_shown_at_full_and_in_list(self):
        col = COLUMNS_BY_ID["billing_code"]
        assert col.description and col.header
        assert not any(resolve_column_visible(col, lvl, {}) for lvl in ("low", "med", "high"))
        assert resolve_column_visible(col, "full", {})
        assert "full" in col.detail_levels  # overcode list's default detail


class TestEngine:
    def test_the_view_carries_the_code_to_the_row(self):
        session = SimpleNamespace(name="alpha", billing_code="alpha", stats=None)
        out = session_with_view(session, {"billing_code": "client-a"})
        assert out.billing_code == "client-a"
        assert session.billing_code == "alpha"  # the shared snapshot is not written

    def test_published_in_the_session_daemon_state(self):
        assert SessionDaemonState(session_id="s", name="a", billing_code="c").to_dict()["billing_code"] == "c"

    def test_the_daemon_publishes_the_code_and_follows_a_rebill(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        daemon = MonitorDaemon.for_test(tmp_path / "state")
        sm = daemon.session_manager
        s = sm.create_session(name="alpha", tmux_session="agents", tmux_window="alpha-1", command=[])
        assert daemon.track_session_stats(sm.get_session(s.id), "running").billing_code == "alpha"
        sm.rebill(s.id, "client-a")
        assert daemon.track_session_stats(sm.get_session(s.id), "running").billing_code == "client-a"

    def test_a_record_from_before_codes_publishes_its_name(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        daemon = MonitorDaemon.for_test(tmp_path / "state")
        session = daemon.session_manager.create_session(
            name="old", tmux_session="agents", tmux_window="old-1", command=[])
        session.billing_code = None
        assert daemon.track_session_stats(session, "running").billing_code == "old"

    def test_sisters_send_the_code_and_the_poller_reads_it(self):
        from overcode.sister_poller import _agent_to_session
        from overcode.web_api import _build_status_info

        info = _build_status_info(SessionDaemonState(session_id="s", name="a", billing_code="client-a"))
        assert info["billing_code"] == "client-a"
        assert _agent_to_session({"name": "a", "status": "running", "billing_code": "client-a"}, "host").billing_code == "client-a"
        assert _agent_to_session({"name": "a", "status": "running"}, "host").billing_code is None  # an older sister
