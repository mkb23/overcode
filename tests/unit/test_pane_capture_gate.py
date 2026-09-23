"""pane_capture_gate: which panes a loop captures, and what the detectors read otherwise."""

import pytest

from overcode.pane_capture_gate import KEEPALIVE_SECONDS, PaneCaptureGate, PaneChangeTracker


class TestPaneChangeTracker:
    def test_first_sight_is_due_and_settles(self):
        t = PaneChangeTracker()
        assert t.due("s1", (1, 0, 0, 0, "claude"), None, 100.0)
        assert not t.due("s1", (1, 0, 0, 0, "claude"), None, 102.0)
        assert not t.due("s1", (1, 0, 0, 0, "claude"), None, 104.0)

    def test_a_signature_change_is_due_then_one_follow_up_then_settles(self):
        """The follow-up is the polling detector's running-to-waiting step: it
        needs to see the same content twice, as it did with a capture per loop."""
        t = PaneChangeTracker()
        t.due("s1", (1, 0, 0, 0, "claude"), None, 100.0)
        assert t.due("s1", (2, 0, 0, 0, "claude"), None, 102.0)  # changed
        assert t.due("s1", (2, 0, 0, 0, "claude"), None, 104.0)  # follow-up
        assert not t.due("s1", (2, 0, 0, 0, "claude"), None, 106.0)

    def test_a_change_during_the_follow_up_starts_another(self):
        t = PaneChangeTracker()
        t.due("s1", (1,), None, 100.0)
        assert t.due("s1", (2,), None, 102.0)
        assert t.due("s1", (3,), None, 104.0)  # changed again
        assert t.due("s1", (3,), None, 106.0)  # its follow-up
        assert not t.due("s1", (3,), None, 108.0)

    def test_extra_key_change_is_due(self):
        """The daemon passes the hook_state stat: a hook event with no pane change."""
        t = PaneChangeTracker()
        t.due("s1", (1,), (10, 50), 100.0)
        assert not t.due("s1", (1,), (10, 50), 102.0)
        assert t.due("s1", (1,), (11, 52), 104.0)

    def test_none_signature_is_a_value(self):
        """A vanished window (None) differs from a live one, and stays settled while gone."""
        t = PaneChangeTracker()
        t.due("s1", (1,), None, 100.0)
        assert t.due("s1", None, None, 102.0)  # gone
        assert t.due("s1", None, None, 104.0)  # follow-up
        assert not t.due("s1", None, None, 106.0)
        assert t.due("s1", (7,), None, 108.0)  # back

    def test_keepalive_recaptures_an_unchanged_pane(self):
        t = PaneChangeTracker(keepalive_seconds=5.0)
        t.due("s1", (1,), None, 100.0)
        assert not t.due("s1", (1,), None, 104.9)
        assert t.due("s1", (1,), None, 105.0)
        assert not t.due("s1", (1,), None, 107.0)  # a keepalive capture has no follow-up
        assert t.due("s1", (1,), None, 110.0)

    def test_default_keepalive(self):
        assert PaneChangeTracker().keepalive_seconds == KEEPALIVE_SECONDS == 5.0

    def test_keys_are_independent_and_forgettable(self):
        t = PaneChangeTracker()
        assert t.due("a", (1,), None, 0.0) and t.due("b", (1,), None, 0.0)
        assert not t.due("a", (1,), None, 1.0)
        assert t.due("b", (2,), None, 1.0)
        assert t.last_captured_at("a") == 0.0 and t.last_captured_at("b") == 1.0
        assert len(t) == 2
        t.forget({"b"})
        assert len(t) == 1 and t.last_captured_at("a") is None
        assert t.due("a", (1,), None, 2.0)  # forgotten = first sight again


class TestPaneCaptureGate:
    def _raw(self, log, texts):
        def raw(window, lines):
            log.append((window, lines))
            return texts.get(window)

        return raw

    def test_unplanned_window_is_always_a_raw_capture(self):
        gate, log = PaneCaptureGate(), []
        raw = self._raw(log, {"w": "text"})
        gate.begin_loop()
        assert gate.capture("w", 500, raw) == "text"
        assert gate.capture("w", 500, raw) == "text"
        assert log == [("w", 500), ("w", 500)]

    def test_planned_due_window_captures_on_every_read_of_the_loop(self):
        """A detector that reads twice (SessionEnd re-check) sees two captures, as before."""
        gate, log = PaneCaptureGate(), []
        raw = self._raw(log, {"w": "text"})
        gate.begin_loop()
        gate.plan("w", True)
        gate.capture("w", 500, raw)
        gate.capture("w", 500, raw)
        assert len(log) == 2

    def test_planned_not_due_window_serves_the_last_text(self):
        gate, log = PaneCaptureGate(), []
        texts = {"w": "first"}
        raw = self._raw(log, texts)
        gate.begin_loop()
        gate.plan("w", True)
        assert gate.capture("w", 500, raw) == "first"
        texts["w"] = "second"  # tmux moved on, the loop did not see it
        gate.begin_loop()
        gate.plan("w", False)
        assert gate.capture("w", 500, raw) == "first"
        assert len(log) == 1
        assert (gate.raw_captures, gate.served_from_cache) == (1, 1)

    def test_never_captured_depth_is_a_raw_capture_even_when_not_due(self):
        gate, log = PaneCaptureGate(), []
        raw = self._raw(log, {"w": "text"})
        gate.begin_loop()
        gate.plan("w", True)
        gate.capture("w", 500, raw)
        gate.begin_loop()
        gate.plan("w", False)
        gate.capture("w", 550, raw)  # a different detector's depth
        assert log == [("w", 500), ("w", 550)]

    def test_none_is_cached_too(self):
        """A vanished window keeps answering None (terminated) without a command."""
        gate, log = PaneCaptureGate(), []
        raw = self._raw(log, {})
        gate.begin_loop()
        gate.plan("w", True)
        assert gate.capture("w", 500, raw) is None
        gate.begin_loop()
        gate.plan("w", False)
        assert gate.capture("w", 500, raw) is None
        assert len(log) == 1

    def test_forget_drops_dead_windows_only(self):
        gate = PaneCaptureGate()
        raw = self._raw([], {"a": "1", "b": "2"})
        gate.begin_loop()
        gate.plan("a", True)
        gate.plan("b", True)
        gate.capture("a", 500, raw)
        gate.capture("b", 500, raw)
        gate.capture("b", 550, raw)
        assert len(gate) == 3
        gate.forget({"a"})
        assert len(gate) == 1

    def test_begin_loop_clears_the_plan(self):
        gate, log = PaneCaptureGate(), []
        raw = self._raw(log, {"w": "text"})
        gate.begin_loop()
        gate.plan("w", False)
        gate.begin_loop()
        gate.capture("w", 500, raw)  # unplanned now -> raw
        assert len(log) == 1
