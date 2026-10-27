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
