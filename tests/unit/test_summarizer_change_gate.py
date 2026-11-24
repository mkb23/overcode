"""The summarizer asks and captures only when a pane has changed (#528, audit R16)."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from overcode.summarizer_component import (
    SummarizerComponent,
    SummarizerConfig,
    normalise_for_change,
)
from overcode.tmux_utils import PaneInfo

BUSY = "✶ Pondering… ({secs}s · ↑ {tok}k tokens · esc to interrupt)"


def _pane(text_lines, busy=None):
    lines = list(text_lines)
    if busy:
        lines.append(busy)
    lines.append("⏵⏵ bypass permissions on (shift+tab to cycle)")
    return "\n".join(lines)


class TestNormalise:

    def test_spinner_frames_and_ticking_counters_are_not_changes(self):
        a = _pane(["did a thing"], BUSY.format(secs=3, tok="1.1"))
        b = _pane(["did a thing"], BUSY.format(secs=4, tok="1.3").replace("✶", "✻"))
        assert normalise_for_change(a) == normalise_for_change(b)

    def test_new_output_is_a_change(self):
        a = _pane(["did a thing"], BUSY.format(secs=3, tok="1.1"))
        b = _pane(["did a thing", "and another"], BUSY.format(secs=3, tok="1.1"))
        assert normalise_for_change(a) != normalise_for_change(b)

    def test_ansi_and_status_bar_do_not_count(self):
        assert normalise_for_change("\x1b[31mred\x1b[0m\n⏵⏵ on") == "red"

    def test_only_the_tail_counts(self):
        assert normalise_for_change("old\nkept", lines=1) == "kept"


def _session(sid="s1", window="w1"):
    s = Mock()
    s.id = sid
    s.name = f"agent-{sid}"
    s.tmux_window = window
    s.stats = Mock(current_state="running")
    return s


@pytest.fixture
def component():
    tmux = Mock()
    tmux.list_panes.return_value = None
    tmux.capture_pane.return_value = _pane(["working"], BUSY.format(secs=1, tok="1"))
    client = Mock()
    client.summarize.return_value = "a summary"
    c = SummarizerComponent(
        tmux_session="agents", tmux=tmux,
        config=SummarizerConfig(enabled=True, interval=0.0, context_interval=0.0),
    )
    c._client = client
    return c


def _calls(c, mode):
    return [k for k in c._client.summarize.call_args_list if k.kwargs["mode"] == mode]


class TestApiCallsGatedOnChange:

    def test_unchanged_pane_makes_no_short_or_context_call(self, component):
        s = _session()
        component.update([s])
        assert len(_calls(component, "short")) == 1 and len(_calls(component, "context")) == 1
        # only the busy line ticked
        component.tmux.capture_pane.return_value = _pane(["working"], BUSY.format(secs=9, tok="2"))
        component.update([s])
        component.update([s])
        assert len(_calls(component, "short")) == 1
        assert len(_calls(component, "context")) == 1  # was: every 15 s regardless

    def test_a_change_gets_both_summaries_again(self, component):
        s = _session()
        component.update([s])
        component.tmux.capture_pane.return_value = _pane(["working", "wrote foo.py"])
        component.update([s])
        assert len(_calls(component, "short")) == 2 and len(_calls(component, "context")) == 2

    def test_a_change_seen_while_short_was_rate_limited_is_summarised_later(self, component):
        """The short summary's hash moves only when a short summary is made."""
        s = _session()
        component.config.interval = 5.0
        component.update([s])
        component.tmux.capture_pane.return_value = _pane(["working", "new step"])
        component.update([s])  # short rate-limited; context runs on the change
        assert len(_calls(component, "short")) == 1
        component._last_update[s.id] = datetime.now() - timedelta(seconds=10)
        component.update([s])
        assert len(_calls(component, "short")) == 2

    def test_a_failed_call_is_retried_on_the_same_content(self, component):
        s = _session()
        component._client.summarize.side_effect = [Exception("api down"), "ok", "ok", "ok"]
        component.config.context_interval = 1000.0
        component._last_context_update[s.id] = datetime.now()
        component.update([s])
        component.update([s])
        assert len(_calls(component, "short")) == 2


def _info(activity, history=10):
    return PaneInfo("w1", 1, 100, activity, history, 0, 5, "claude", 1)


class TestCaptureGatedOnTheListing:

    def test_an_unchanged_pane_is_not_captured_again(self, component, monkeypatch):
        import overcode.summarizer_component as sc
        monkeypatch.setattr(sc.time, "time", lambda: 1000.5)
        s = _session()
        component.tmux.list_panes.return_value = {"w1": _info(990)}
        component.update([s])
        component.update([s])
        component.update([s])
        assert component.tmux.capture_pane.call_count == 1
        assert component.tmux.list_panes.call_count == 3  # one listing per pass

    def test_activity_in_the_listing_second_is_captured_again(self, component, monkeypatch):
        import overcode.summarizer_component as sc
        monkeypatch.setattr(sc.time, "time", lambda: 1000.5)
        s = _session()
        component.tmux.list_panes.return_value = {"w1": _info(1000)}
        component.update([s])
        component.update([s])
        assert component.tmux.capture_pane.call_count == 2

    def test_a_moved_signature_is_captured(self, component, monkeypatch):
        import overcode.summarizer_component as sc
        monkeypatch.setattr(sc.time, "time", lambda: 1000.5)
        s = _session()
        component.tmux.list_panes.return_value = {"w1": _info(990)}
        component.update([s])
        component.tmux.list_panes.return_value = {"w1": _info(990, history=11)}
        component.update([s])
        assert component.tmux.capture_pane.call_count == 2

    def test_without_a_listing_every_pass_captures(self, component):
        s = _session()
        component.update([s])
        component.update([s])
        assert component.tmux.capture_pane.call_count == 2


class TestDeadRowsAndPruning:
    """Gone windows cost nothing; gone agents leave no state behind (#528)."""

    def test_a_window_missing_from_the_listing_is_not_captured(self, component):
        component.tmux.list_panes.return_value = {"other": _info(990)}
        component.update([_session()])
        assert component.tmux.capture_pane.call_count == 0
        assert component._client.summarize.call_count == 0

    def test_an_empty_listing_means_every_window_is_gone(self, component):
        component.tmux.list_panes.return_value = {}
        component.update([_session()])
        assert component.tmux.capture_pane.call_count == 0

    def test_a_failed_listing_still_captures(self, component):
        component.tmux.list_panes.return_value = None
        component.update([_session()])
        assert component.tmux.capture_pane.call_count == 1

    def test_state_for_gone_agents_is_dropped(self, component):
        a, b = _session("a", "wa"), _session("b", "wb")
        component.update([a, b])
        held = component.summaries
        assert set(held) == {"a", "b"}
        component.update([b])
        for d in (component.summaries, component._last_update, component._last_context_update,
                  component._last_content_hash, component._last_context_hash,
                  component._content_hashes):
            assert "a" not in d
        # rebound, not edited: the TUI's dict from the last pass is intact
        assert set(held) == {"a", "b"}

    def test_captures_for_gone_agents_are_dropped(self, component, monkeypatch):
        import overcode.summarizer_component as sc
        monkeypatch.setattr(sc.time, "time", lambda: 1000.5)
        component.tmux.list_panes.return_value = {"wa": _info(990), "wb": _info(990)}
        component.update([_session("a", "wa"), _session("b", "wb")])
        assert len(component._captured) == 2
        component.update([_session("b", "wb")])
        assert len(component._captured) == 1


class TestHashReuse:

    def test_a_reused_capture_is_not_normalised_again(self, component, monkeypatch):
        import overcode.summarizer_component as sc
        monkeypatch.setattr(sc.time, "time", lambda: 1000.5)
        calls = []
        real = sc.normalise_for_change
        monkeypatch.setattr(sc, "normalise_for_change", lambda c, n: calls.append(1) or real(c, n))
        s = _session()
        component.tmux.list_panes.return_value = {"w1": _info(990)}
        for _ in range(3):
            component.update([s])
        assert component.tmux.capture_pane.call_count == 1
        assert len(calls) == 1

    def test_a_new_capture_is_hashed_afresh(self, component):
        s = _session()
        component.update([s])
        component.tmux.capture_pane.return_value = _pane(["working", "more"])
        component.update([s])
        assert len(_calls(component, "short")) == 2


class TestParallelCalls:
    """Summary calls run on a bounded pool; captures stay on the pass thread."""

    def _fleet(self, n):
        return [_session(f"s{i}", f"w{i}") for i in range(n)]

    def test_calls_overlap_and_stay_bounded(self, component):
        import threading
        import time as _time
        from overcode.summarizer_component import SUMMARY_WORKERS
        lock = threading.Lock()
        state = {"now": 0, "peak": 0}

        def slow(**kwargs):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            _time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return "x"

        component._client.summarize.side_effect = slow
        component.update(self._fleet(12))
        assert component._client.summarize.call_count == 24
        assert component.total_calls == 24
        assert 1 < state["peak"] <= SUMMARY_WORKERS
        assert all(component.summaries[f"s{i}"].text == "x" for i in range(12))

    def test_the_pass_returns_after_its_calls(self, component):
        import time as _time

        def slow(**kwargs):
            _time.sleep(0.05)
            return "done"

        component._client.summarize.side_effect = slow
        result = component.update(self._fleet(5))
        assert all(result[f"s{i}"].context == "done" for i in range(5))

    def test_stop_mid_pass_waits_for_calls_in_flight(self, component):
        import time as _time

        def slow(**kwargs):
            _time.sleep(0.05)
            return "done"

        component._client.summarize.side_effect = slow
        checks = {"n": 0}

        def stop_after_three():
            checks["n"] += 1
            return checks["n"] > 3

        result = component.update(self._fleet(6), should_stop=stop_after_three)
        assert sorted(result) == ["s0", "s1", "s2"]
        assert all(result[k].text == "done" for k in result)
        assert component._next_index == 3

    def test_cost_is_summed_per_call(self, component):
        from overcode.pricing import estimate_cost
        component._client.model = "gpt-4o-mini"
        component._client.last_input_tokens = 1000
        component._client.last_output_tokens = 10
        component.update(self._fleet(8))
        assert component.total_cost_usd == pytest.approx(16 * estimate_cost("gpt-4o-mini", 1000, 10))


def test_client_token_usage_is_per_thread():
    import threading
    from overcode.summarizer_client import SummarizerClient
    client = SummarizerClient.__new__(SummarizerClient)
    client.last_input_tokens = 5
    seen = []

    def other():
        seen.append(client.last_input_tokens)
        client.last_input_tokens = 9
        seen.append(client.last_input_tokens)

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen == [0, 9]
    assert client.last_input_tokens == 5
