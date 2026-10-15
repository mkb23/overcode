"""A sister's agents in this TUI: what its poll carries onto the row.

Sisters stay on HTTP polling; each poll builds a fresh remote Session and
``SessionSummary.apply_remote`` refreshes the existing row from it.
Synthetic agent dicts only.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from overcode.sister_poller import _agent_to_session
from overcode.tui_widgets.session_summary import SessionSummary

pytestmark = pytest.mark.unit


def _agent(status, seconds_in_state, daemon_state=None):
    agent = {"name": "far", "status": status, "activity": "x",
             "time_in_state_raw": float(seconds_in_state)}
    if daemon_state is not None:
        agent["daemon_state"] = daemon_state
    return agent


def _seconds_in_state(row):
    return (datetime.now() - row._status_changed_at).total_seconds()


class TestTimeInState:
    """A remote row's time in state follows each poll, not its first one."""

    def test_follows_a_status_change_from_an_older_sister(self):
        row = SessionSummary(_agent_to_session(_agent("running", 3 * 3600), "host"))
        assert _seconds_in_state(row) > 3 * 3600 - 60
        row.session = _agent_to_session(_agent("waiting_user", 5), "host")
        row.apply_remote()
        assert _seconds_in_state(row) < 60

    def test_counts_from_a_0_6_sister_s_published_episode(self):
        start = (datetime.now() - timedelta(seconds=30)).timestamp()
        state = {"current_status": "waiting_user", "live_colour": "color_red",
                 "episode_colour": "color_red", "episode_start": start,
                 "live_since": start, "input_needed_since": start}
        row = SessionSummary(_agent_to_session(_agent("running", 3 * 3600), "host"))
        row.session = _agent_to_session(_agent("waiting_user", 7200, state), "host")
        row.apply_remote()
        assert 20 < _seconds_in_state(row) < 60


class TestOlderSisterStalls:
    """A pre-0.6 sister publishes no input_needed_since; its waiting_user
    agent still gets the unvisited marker and a notification."""

    def _state(self, status, since):
        return {"current_status": status, "status_since": since.isoformat()}

    def test_waiting_user_marks_the_row_unvisited(self):
        since = datetime.now() - timedelta(seconds=40)
        session = _agent_to_session(
            _agent("waiting_user", 40, self._state("waiting_user", since)), "host")
        assert session.remote_daemon_state["input_needed_since"] == pytest.approx(
            since.timestamp())
        row = SessionSummary(session)
        row.apply_remote()
        assert row.is_unvisited_stalled
        row.apply_remote(visited_here=datetime.now().timestamp())
        assert not row.is_unvisited_stalled

    def test_a_working_agent_needs_no_input(self):
        session = _agent_to_session(
            _agent("running", 40, self._state("running", datetime.now())), "host")
        assert session.remote_daemon_state["input_needed_since"] is None

    def test_a_0_6_sister_s_value_is_kept(self):
        state = {"current_status": "waiting_user", "input_needed_since": None,
                 "status_since": datetime.now().isoformat()}
        session = _agent_to_session(_agent("waiting_user", 40, state), "host")
        assert session.remote_daemon_state["input_needed_since"] is None

    def test_a_new_stall_notifies(self):
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI.__new__(SupervisorTUI)
        app._visited_here = {}
        app._remote_stretch_seen = {}
        app._notifier = MagicMock()
        start = datetime.now() - timedelta(minutes=5)
        row = SessionSummary(_agent_to_session(
            _agent("running", 300, self._state("running", start)), "host"))
        app._apply_remote(row)  # first sight, working
        since = datetime.now() - timedelta(seconds=3)
        row.session = _agent_to_session(
            _agent("waiting_user", 3, self._state("waiting_user", since)), "host")
        app._apply_remote(row)
        row.session = _agent_to_session(
            _agent("waiting_user", 5, self._state("waiting_user", since)), "host")
        app._apply_remote(row)  # same stretch, polled again
        app._notifier.queue.assert_called_once_with("far", "x")


class TestSisterInAnotherZone:
    """A sister's ``status_since`` is naive local time in *its* zone: read
    as this host's, a sister ahead put its agent's state hours in the
    future (negative time in state, unvisited for hours after a visit)."""

    @staticmethod
    def _sister_since(hours_ahead, seconds_ago):
        from datetime import timezone
        zone = timezone(timedelta(hours=hours_ahead))
        return (datetime.now(zone) - timedelta(seconds=seconds_ago)).replace(tzinfo=None)

    @pytest.mark.parametrize("hours_ahead", [9, -7, 5.75])
    def test_time_in_state_is_the_sister_s(self, hours_ahead):
        since = self._sister_since(hours_ahead, 120)
        state = {"current_status": "waiting_user", "status_since": since.isoformat()}
        row = SessionSummary(_agent_to_session(_agent("running", 3 * 3600), "host"))
        row.session = _agent_to_session(_agent("waiting_user", 120, state), "host")
        row.apply_remote()
        assert 110 < _seconds_in_state(row) < 180

    @pytest.mark.parametrize("status", ["waiting_user", "done"])
    def test_status_since_is_placed_in_this_host_s_zone(self, status):
        import time
        since = self._sister_since(9, 120)
        state = {"current_status": status, "status_since": since.isoformat(),
                 "input_needed_since": None}
        session = _agent_to_session(_agent(status, 120, state), "host")
        moved = datetime.fromisoformat(session.remote_daemon_state["status_since"])
        assert moved.timestamp() == pytest.approx(time.time() - 120, abs=5)

    def test_a_visit_clears_the_unvisited_marker(self):
        import time
        since = self._sister_since(9, 120)
        state = {"current_status": "waiting_user", "status_since": since.isoformat()}
        session = _agent_to_session(_agent("waiting_user", 120, state), "host")
        assert session.remote_daemon_state["input_needed_since"] == pytest.approx(
            time.time() - 120, abs=5)
        row = SessionSummary(session)
        row.apply_remote()
        assert row.is_unvisited_stalled
        row.apply_remote(visited_here=time.time())
        assert not row.is_unvisited_stalled

    def test_the_stretch_start_holds_still_between_polls(self):
        since = self._sister_since(9, 120).isoformat()
        starts = {
            _agent_to_session(_agent("waiting_user", raw, {
                "current_status": "waiting_user", "status_since": since}), "host"
            ).remote_daemon_state["input_needed_since"]
            for raw in (119.0, 120.0, 121.5)
        }
        assert len(starts) == 1  # else every poll would notify again
