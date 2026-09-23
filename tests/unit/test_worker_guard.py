"""Tests for overcode.worker_guard: the single-flight guard on TUI thread workers.

Textual's ``exclusive=True`` cancels only the task awaiting a thread worker,
so a periodic worker slower than its period used to stack threads (audit
R2). These tests pin the guard's semantics and check that every periodic
thread worker in the TUI actually carries it.
"""

import threading
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode import worker_guard  # noqa: E402
from overcode.worker_guard import (  # noqa: E402
    is_in_flight,
    single_flight,
    worker_cancelled,
)


class _Slow:
    """A worker-like object whose body blocks until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.runs = 0

    @single_flight("slow")
    def tick(self, tag="run"):
        self.runs += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return tag

    @single_flight("other")
    def other(self):
        return "other-ran"

    @single_flight("boom")
    def boom(self):
        raise RuntimeError("boom")


class TestSingleFlight:
    def test_second_call_is_skipped_while_first_runs(self):
        obj = _Slow()
        results = {}

        def first():
            results["first"] = obj.tick("first")

        t = threading.Thread(target=first)
        t.start()
        assert obj.entered.wait(timeout=5)
        assert is_in_flight(obj, "slow")

        # A second tick arrives while the first is still executing
        assert obj.tick("second") is None
        assert obj.runs == 1

        obj.release.set()
        t.join(timeout=5)
        assert results["first"] == "first"
        assert not is_in_flight(obj, "slow")

    def test_runs_again_once_the_previous_run_finished(self):
        obj = _Slow()
        obj.release.set()
        assert obj.tick("a") == "a"
        assert obj.tick("b") == "b"
        assert obj.runs == 2

    def test_lock_released_when_body_raises(self):
        obj = _Slow()
        with pytest.raises(RuntimeError):
            obj.boom()
        assert not is_in_flight(obj, "boom")
        with pytest.raises(RuntimeError):
            obj.boom()

    def test_groups_are_independent_on_one_instance(self):
        obj = _Slow()
        t = threading.Thread(target=obj.tick)
        t.start()
        assert obj.entered.wait(timeout=5)
        assert obj.other() == "other-ran"  # different group: not blocked
        obj.release.set()
        t.join(timeout=5)

    def test_instances_are_independent(self):
        a, b = _Slow(), _Slow()
        t = threading.Thread(target=a.tick)
        t.start()
        assert a.entered.wait(timeout=5)
        b.release.set()
        assert b.tick("b") == "b"  # same group, other instance: not blocked
        a.release.set()
        t.join(timeout=5)

    def test_many_concurrent_ticks_run_exactly_one(self):
        obj = _Slow()
        outcomes = []
        threads = [threading.Thread(target=lambda: outcomes.append(obj.tick())) for _ in range(20)]
        for t in threads:
            t.start()
        assert obj.entered.wait(timeout=5)
        obj.release.set()
        for t in threads:
            t.join(timeout=5)
        assert obj.runs == 1
        assert outcomes.count("run") == 1
        assert outcomes.count(None) == 19

    def test_works_on_instances_built_without_init(self):
        obj = _Slow.__new__(_Slow)  # no __init__: the lock registry is lazy
        obj.runs = 0
        obj.entered = threading.Event()
        obj.release = threading.Event()
        obj.release.set()
        assert obj.tick("x") == "x"

    def test_group_is_introspectable(self):
        assert _Slow.tick.single_flight_group == "slow"
        assert is_in_flight(_Slow(), "slow") is False


class _StubWorker:
    def __init__(self):
        self.is_cancelled = False


class TestWorkerCancelled:
    def test_false_outside_a_worker(self):
        assert worker_cancelled() is False

    def test_reflects_the_current_worker_flag(self, monkeypatch):
        stub = _StubWorker()
        monkeypatch.setattr(worker_guard, "get_current_worker", lambda: stub)
        assert worker_cancelled() is False
        stub.is_cancelled = True
        assert worker_cancelled() is True

    def test_a_per_agent_loop_stops_early_when_cancelled(self, monkeypatch):
        """The pattern the TUI loops use: check between agents, stop, keep progress."""
        stub = _StubWorker()
        monkeypatch.setattr(worker_guard, "get_current_worker", lambda: stub)
        done = []

        def loop(agents):
            for agent in agents:
                if worker_cancelled():
                    break
                done.append(agent)
                if len(done) == 3:
                    stub.is_cancelled = True  # a newer tick supersedes this run

        loop(list(range(10)))
        assert done == [0, 1, 2]


class TestTuiWorkersAreGuarded:
    """Every periodic thread worker in the TUI must carry the guard.

    The fast-status path keeps its own main-thread flag
    (_status_update_in_progress) so it is never coupled to slower groups.
    """

    WORKERS = {
        "_fetch_daemon_status_async": "daemon_status",
        "_fetch_timeline_async": "timeline",
        "_resize_agent_windows_async": "agent_resize",
        "_fetch_sessions_async": "refresh_sessions",
        "_update_stats_async": "slow_stats",
        "_poll_sisters_async": "sister_poll",
        "_poll_focused_sister_async": "focused_sister_poll",
        "_update_summaries_async": "summarizer",
        "_refresh_jobs": "refresh_jobs",
        "_provision_ssh_sisters": "ssh_provision",
    }

    @pytest.mark.parametrize("method,group", sorted(WORKERS.items()))
    def test_worker_has_single_flight_guard(self, method, group):
        from overcode.tui import SupervisorTUI

        decorated = getattr(SupervisorTUI, method)
        # @work keeps the wrapped callable via functools.wraps
        inner = getattr(decorated, "__wrapped__", None)
        assert inner is not None, f"{method} is not a @work method"
        assert getattr(inner, "single_flight_group", None) == group

    def test_guard_skips_a_second_run_of_a_real_tui_worker(self):
        """Drive the real worker body (via __wrapped__) on a bare app instance."""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI.__new__(SupervisorTUI)
        body = SupervisorTUI._fetch_sessions_async.__wrapped__
        entered, release = threading.Event(), threading.Event()
        applied = []

        class _Launcher:
            def list_sessions(self):
                entered.set()
                release.wait(timeout=5)
                return ["s1"]

        app.launcher = _Launcher()
        app.call_from_thread = lambda fn, *a, **kw: applied.append(a)

        t = threading.Thread(target=body, args=(app,))
        t.start()
        assert entered.wait(timeout=5)
        assert body(app) is None  # second tick skipped while the first runs
        release.set()
        t.join(timeout=5)
        assert applied == [(["s1"],)]
