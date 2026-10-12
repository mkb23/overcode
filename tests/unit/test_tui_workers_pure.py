"""The status bar and timeline workers read no widget and no app state off-thread.

Their inputs are built on the main thread and passed in; they hand plain
data back through call_from_thread, applied on the main thread. The
workers are driven here on a stand-in that has none of the app's state:
touching any of it would raise.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from overcode.tui import SupervisorTUI
from overcode.tui_widgets.daemon_status_bar import LocalBarState


def _body(worker):
    """The worker's own body, under @work and @single_flight."""
    return worker.__wrapped__.__wrapped__


def _stand_in(**services):
    return SimpleNamespace(call_from_thread=MagicMock(), _apply_daemon_status="apply-bar",
                           _apply_timeline="apply-timeline", **services)


class TestDaemonStatusWorker:
    def test_reads_only_its_arguments(self):
        app = _stand_in(_usage_monitor=MagicMock())
        state = LocalBarState(supervisor_running=True, baseline_minutes=30)
        with patch("overcode.tui.read_local_state", return_value=state) as read:
            _body(SupervisorTUI._fetch_daemon_status_async)(app, "agents", 30, ["a"], True)
        read.assert_called_once_with("agents", 30, ["a"])
        app._usage_monitor.fetch.assert_called_once_with()
        app.call_from_thread.assert_called_once_with("apply-bar", state)

    def test_usage_is_fetched_only_when_asked(self):
        app = _stand_in(_usage_monitor=MagicMock())
        with patch("overcode.tui.read_local_state", return_value=LocalBarState()):
            _body(SupervisorTUI._fetch_daemon_status_async)(app, "agents", 0, [], False)
        app._usage_monitor.fetch.assert_not_called()


class TestTimelineWorker:
    def test_reads_only_its_arguments(self):
        app = _stand_in(_sister_poller=MagicMock())
        app._sister_poller.poll_all_timelines.return_value = {"remote": [("t", "running")]}
        sessions = [SimpleNamespace(name="a")]
        with patch("overcode.tui.read_history_data",
                   return_value=(["p"], {"a": [("t", "waiting")]})) as read:
            _body(SupervisorTUI._fetch_timeline_async)(app, sessions, "agents", 3.0, True)
        read.assert_called_once_with("agents", 3.0)
        app._sister_poller.poll_all_timelines.assert_called_once_with(3.0)
        app.call_from_thread.assert_called_once_with(
            "apply-timeline", sessions, ["p"],
            {"a": [("t", "waiting")], "remote": [("t", "running")]})

    def test_no_sisters_no_remote_poll(self):
        app = _stand_in(_sister_poller=MagicMock())
        with patch("overcode.tui.read_history_data", return_value=([], {})):
            _body(SupervisorTUI._fetch_timeline_async)(app, [], "agents", 3.0, False)
        app._sister_poller.poll_all_timelines.assert_not_called()


class TestLocalBarState:
    def test_apply_puts_the_read_on_the_bar(self):
        from overcode.tui_widgets import DaemonStatusBar
        bar = DaemonStatusBar.__new__(DaemonStatusBar)
        bar.apply_local_state(LocalBarState(
            supervisor_running=True, summarizer_available=True, web_running=True,
            web_url="http://x", baseline_minutes=15, mean_spin=1.5, spin_sample_count=4))
        assert (bar._supervisor_running, bar._summarizer_available, bar._web_running,
                bar._web_url, bar._spin_baseline_minutes, bar._mean_spin,
                bar._spin_sample_count) == (True, True, True, "http://x", 15, 1.5, 4)
