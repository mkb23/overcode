"""Tests for overcode.worker_guard: the coalescing single-flight guard on TUI workers.

Textual's ``exclusive=True`` cancels only the task awaiting a thread worker,
so a periodic worker slower than its period used to stack threads (audit
R2). The guard runs one call per group at a time and coalesces the rest
into one rerun, so an explicit refresh arriving mid-run is never lost.
These tests pin that contract and check that every periodic thread worker
in the TUI carries the guard without ``exclusive``.
"""

import inspect
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
        self.tags = []
        self.raise_on_run = None  # run number (1-based) whose body raises
        self.gates = {}  # run number -> Event to wait on instead of ``release``

    @single_flight("slow")
    def tick(self, tag="run"):
        self.runs += 1
        self.tags.append(tag)
        self.entered.set()
        self.gates.get(self.runs, self.release).wait(timeout=5)
        if self.raise_on_run == self.runs:
            raise RuntimeError("boom")
        return tag

    @single_flight("other")
    def other(self):
        return "other-ran"

    @single_flight("boom")
    def boom(self):
        raise RuntimeError("boom")


def _run_in_thread(fn, *args):
    box = {}

    def target():
        try:
            box["result"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


class TestSingleFlight:
    def test_second_call_returns_at_once_and_reruns_after_the_first(self):
        """The arriving call is not blocked and not lost: the holder reruns it."""
        obj = _Slow()
        t, box = _run_in_thread(obj.tick, "first")
        assert obj.entered.wait(timeout=5)
        assert is_in_flight(obj, "slow")

        # A second call arrives while the first is still executing
        assert obj.tick("second") is None
        assert obj.runs == 1  # not run concurrently

        obj.release.set()
        t.join(timeout=5)
        assert obj.tags == ["first", "second"]  # rerun happened, in order
        assert box["result"] == "second"  # the holder returns the last body's result
        assert not is_in_flight(obj, "slow")

    def test_runs_again_once_the_previous_run_finished(self):
        obj = _Slow()
        obj.release.set()
        assert obj.tick("a") == "a"
        assert obj.tick("b") == "b"
        assert obj.runs == 2

    def test_no_rerun_when_nothing_arrived_during_the_run(self):
        obj = _Slow()
        obj.release.set()
        assert obj.tick("only") == "only"
        assert obj.runs == 1

    def test_rerun_uses_the_latest_coalesced_arguments(self):
        """Three calls during one run collapse into a single rerun with the newest args."""
        obj = _Slow()
        t, _ = _run_in_thread(obj.tick, "first")
        assert obj.entered.wait(timeout=5)
        assert obj.tick("a") is None
        assert obj.tick("b") is None
        assert obj.tick("c") is None
        obj.release.set()
        t.join(timeout=5)
        assert obj.tags == ["first", "c"]
        assert obj.runs == 2

    def test_a_call_during_the_rerun_is_coalesced_again(self):
        """Back-to-back reruns chain until a body completes with nothing queued."""
        obj = _Slow()
        first_gate, rerun_gate = threading.Event(), threading.Event()
        obj.gates = {1: first_gate, 2: rerun_gate}
        obj.release.set()  # any later run does not block
        t, _ = _run_in_thread(obj.tick, "first")
        assert obj.entered.wait(timeout=5)
        assert obj.tick("second") is None
        obj.entered.clear()
        first_gate.set()  # first body finishes; the rerun starts and blocks
        assert obj.entered.wait(timeout=5)
        assert obj.tags == ["first", "second"]
        assert obj.tick("third") is None  # arrives during the rerun
        rerun_gate.set()
        t.join(timeout=5)
        assert obj.tags == ["first", "second", "third"]
        assert not is_in_flight(obj, "slow")

    def test_group_released_and_rerun_dropped_when_body_raises(self):
        obj = _Slow()
        obj.raise_on_run = 1
        t, box = _run_in_thread(obj.tick, "first")
        assert obj.entered.wait(timeout=5)
        assert obj.tick("queued") is None
        obj.release.set()
        t.join(timeout=5)
        assert isinstance(box["error"], RuntimeError)
        assert obj.tags == ["first"]  # the queued rerun is discarded
        assert not is_in_flight(obj, "slow")
        assert obj.tick("after") == "after"  # group usable again

    def test_lock_released_when_body_raises(self):
        obj = _Slow()
        with pytest.raises(RuntimeError):
            obj.boom()
        assert not is_in_flight(obj, "boom")
        with pytest.raises(RuntimeError):
            obj.boom()

    def test_groups_are_independent_on_one_instance(self):
        obj = _Slow()
        t, _ = _run_in_thread(obj.tick)
        assert obj.entered.wait(timeout=5)
        assert obj.other() == "other-ran"  # different group: not blocked
        obj.release.set()
        t.join(timeout=5)

    def test_instances_are_independent(self):
        a, b = _Slow(), _Slow()
        t, _ = _run_in_thread(a.tick)
        assert a.entered.wait(timeout=5)
        b.release.set()
        assert b.tick("b") == "b"  # same group, other instance: not blocked
        a.release.set()
        t.join(timeout=5)

    def test_many_concurrent_ticks_run_one_plus_one_rerun(self):
        """20 ticks landing together cost two body runs, never a thread stack."""
        obj = _Slow()
        outcomes = []
        threads = [threading.Thread(target=lambda: outcomes.append(obj.tick())) for _ in range(20)]
        for t in threads:
            t.start()
        assert obj.entered.wait(timeout=5)
        obj.release.set()
        for t in threads:
            t.join(timeout=5)
        assert obj.runs == 2  # the holder plus exactly one coalesced rerun
        assert outcomes.count("run") == 1
        assert outcomes.count(None) == 19

    def test_works_on_instances_built_without_init(self):
        obj = _Slow.__new__(_Slow)  # no __init__: the state registry is lazy
        obj.runs = 0
        obj.tags = []
        obj.raise_on_run = None
        obj.gates = {}
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
                    stub.is_cancelled = True  # the app is shutting down

        loop(list(range(10)))
        assert done == [0, 1, 2]


class TestTuiWorkersAreGuarded:
    """Every periodic thread worker in the TUI must carry the guard, non-exclusive.

    The fast-status path keeps its own main-thread flag
    (_status_update_in_progress) so it is never coupled to slower groups.
    ``exclusive=True`` on a guarded worker would cancel the in-flight pass
    that the arriving tick then fails to replace (it is coalesced, not run),
    halving throughput of any pass longer than its period.
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

    @staticmethod
    def _work_options(decorated) -> dict:
        # textual's @work builds a closure over its keyword options
        return inspect.getclosurevars(decorated).nonlocals

    @pytest.mark.parametrize("method,group", sorted(WORKERS.items()))
    def test_worker_has_single_flight_guard_and_is_not_exclusive(self, method, group):
        from overcode.tui import SupervisorTUI

        decorated = getattr(SupervisorTUI, method)
        # @work keeps the wrapped callable via functools.wraps
        inner = getattr(decorated, "__wrapped__", None)
        assert inner is not None, f"{method} is not a @work method"
        assert getattr(inner, "single_flight_group", None) == group
        options = self._work_options(decorated)
        assert options["thread"] is True
        assert options["group"] == group
        assert options["exclusive"] is False, f"{method} must not be exclusive"

    def test_fast_status_worker_is_independent_of_the_guard(self):
        """The 250 ms path is exclusive and unguarded by design (own main-thread flag)."""
        from overcode.tui import SupervisorTUI

        decorated = SupervisorTUI._fetch_statuses_async
        assert getattr(decorated.__wrapped__, "single_flight_group", None) is None
        options = self._work_options(decorated)
        assert options["group"] == "fast_status"
        assert options["exclusive"] is True

    def test_guard_coalesces_a_second_run_of_a_real_tui_worker(self):
        """Drive the real worker body (via __wrapped__) on a bare app instance.

        An explicit refresh_sessions() during the 10 s tick's read must not be
        lost: the tick's thread applies its own result, then reads again.
        """
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI.__new__(SupervisorTUI)
        body = SupervisorTUI._fetch_sessions_async.__wrapped__
        entered, release = threading.Event(), threading.Event()
        applied = []
        reads = []

        class _Launcher:
            def list_sessions(self):
                reads.append(len(reads) + 1)
                entered.set()
                release.wait(timeout=5)
                return [f"read{len(reads)}"]

        app.launcher = _Launcher()
        app.call_from_thread = lambda fn, *a, **kw: applied.append(a)

        t = threading.Thread(target=body, args=(app,))
        t.start()
        assert entered.wait(timeout=5)
        assert body(app) is None  # second call coalesced while the first runs
        assert reads == [1]
        release.set()
        t.join(timeout=5)
        assert reads == [1, 2]
        assert applied == [(["read1"],), (["read2"],)]  # fresher result applied last
