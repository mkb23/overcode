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


class TestRemoteVisitsSurviveARestart:
    """No engine here records a visit to a sister's agent: the TUI keeps
    it, so a restarted TUI does not bring the 🔔 back."""

    SID = "remote:host:far"

    def _visit(self, app, session):
        row = SessionSummary(session)
        app.query = lambda *a, **kw: [row]
        app._visit(session.id)

    def _waiting(self):
        since = datetime.now() - timedelta(seconds=40)
        state = {"current_status": "waiting_user", "status_since": since.isoformat()}
        return _agent_to_session(_agent("waiting_user", 40, state), "host")

    def test_a_visit_is_still_there_after_a_restart(self, make_tui):
        session = self._waiting()
        self._visit(make_tui(sessions=[]), session)
        again = make_tui(sessions=[])
        row = SessionSummary(session)
        row.apply_remote(again._visited_here.get(self.SID))
        assert not row.is_unvisited_stalled

    def test_local_visits_stay_the_engine_s(self, make_tui):
        from overcode.session_manager import Session

        app = make_tui(sessions=[])
        app.engine_link = MagicMock()
        local = Session(id="local-1", name="near", tmux_session="test", tmux_window=1,
                        command=["claude"], start_directory="/tmp",
                        start_time=datetime.now().isoformat())
        self._visit(app, local)
        app.engine_link.visit.assert_called_once_with("local-1")
        assert make_tui(sessions=[])._visited_here == {}

    def test_a_malformed_saved_value_is_ignored(self):
        from overcode.settings import TUIPreferences
        from overcode.tui import remote_visits

        prefs = TUIPreferences(remote_visits={self.SID: "soon", "local": 1.0,
                                              "remote:h:b": 5.0})
        assert remote_visits(prefs) == {"remote:h:b": 5.0}
        assert remote_visits(TUIPreferences(remote_visits=[1])) == {}


class TestSisterInAnotherTimezone:
    """A sister stamps status_since in its own naive local time; the row
    and the unvisited marker read it on this host's clock."""

    # The sister's local clock against ours: ahead (London viewed from New
    # York), behind, and a half-hour zone
    ZONES = [5 * 3600, -8 * 3600, int(5.5 * 3600)]

    def _sister_stamp(self, zone, seconds_ago):
        return (datetime.now() + timedelta(seconds=zone - seconds_ago)).isoformat()

    @pytest.mark.parametrize("zone", ZONES)
    def test_time_in_state_from_an_older_sister(self, zone):
        state = {"current_status": "running", "status_since": self._sister_stamp(zone, 600)}
        row = SessionSummary(_agent_to_session(_agent("running", 600, state), "host"))
        row.apply_remote()
        assert 590 < _seconds_in_state(row) < 660

    @pytest.mark.parametrize("zone", ZONES)
    def test_time_in_state_of_a_0_6_sister_s_lifecycle_state(self, zone):
        state = {"current_status": "asleep", "status_since": self._sister_stamp(zone, 600),
                 "input_needed_since": None}
        row = SessionSummary(_agent_to_session(_agent("asleep", 600, state), "host"))
        row.apply_remote()
        assert 590 < _seconds_in_state(row) < 660

    @pytest.mark.parametrize("zone", ZONES)
    def test_a_visit_clears_and_a_later_stall_marks(self, zone):
        state = {"current_status": "waiting_user", "status_since": self._sister_stamp(zone, 40)}
        session = _agent_to_session(_agent("waiting_user", 40, state), "host")
        assert session.remote_daemon_state["input_needed_since"] == pytest.approx(
            datetime.now().timestamp() - 40, abs=5)
        row = SessionSummary(session)
        row.apply_remote(visited_here=datetime.now().timestamp() - 60)
        assert row.is_unvisited_stalled  # stalled after the last visit
        row.apply_remote(visited_here=datetime.now().timestamp())
        assert not row.is_unvisited_stalled

    def test_the_shift_holds_still_across_polls(self):
        since = self._sister_stamp(5 * 3600, 40)
        state = {"current_status": "waiting_user", "status_since": since}
        first = _agent_to_session(_agent("waiting_user", 40.0, state), "host")
        again = _agent_to_session(_agent("waiting_user", 41.3, state), "host")
        assert (first.remote_daemon_state["input_needed_since"]
                == again.remote_daemon_state["input_needed_since"])
