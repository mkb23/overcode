"""Summarizer spend and captures on a fleet whose panes only tick (#528, audit R16).

Fifty busy agents whose panes show nothing new but a spinner frame and an
elapsed-seconds counter. Before: every 5 s pass captured all 50 panes, each
spinner frame counted as a change (a short call per agent per 5 s), and the
context call ran every 15 s regardless: 16 calls per agent per minute. Now
a normalised hash gates both calls, so after the first summaries nothing is
asked (and one list-panes per pass spares the capture of a pane that has
not moved at all; these spinning panes still move, so they are captured).

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import itertools
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from overcode.summarizer_component import SummarizerComponent, SummarizerConfig
from overcode.tmux_utils import PaneInfo

pytestmark = [pytest.mark.scale]

AGENTS = 50
PASSES = 12  # a minute of 5 s passes


class _TickingFleet:
    """Panes whose only motion is the busy line; the listing says so too."""

    def __init__(self):
        self.frame = itertools.count()
        self.captures = 0
        self.listings = 0
        self.activity = 0

    def list_panes(self, session):
        self.listings += 1
        # A spinner stamps window_activity every second: the signature moves
        self.activity += 5
        return {f"w{i}": PaneInfo(f"w{i}", i, 100 + i, self.activity, 400, 0, 40, "claude", 1)
                for i in range(AGENTS)}

    def capture_pane(self, session, window, lines=100):
        self.captures += 1
        n = next(self.frame)
        glyph = "✶✻✽"[n % 3]
        return (f"{window} edited foo.py\n{glyph} Pondering… ({n}s · ↑ {n / 10:.1f}k tokens"
                " · esc to interrupt)\n⏵⏵ bypass permissions on")


def test_a_ticking_fleet_costs_no_api_calls_after_the_first_summaries():
    fleet = _TickingFleet()
    c = SummarizerComponent(tmux_session="agents", tmux=fleet, config=SummarizerConfig(enabled=True))
    c._client = Mock()
    c._client.summarize.return_value = "editing foo.py"
    sessions = []
    for i in range(AGENTS):
        s = Mock(id=f"s{i}", tmux_window=f"w{i}", stats=Mock(current_state="running"))
        s.name = f"agent{i}"
        sessions.append(s)

    for _ in range(PASSES):
        c.update(sessions)
        # every rate limit has elapsed by the next pass (5 s and 15 s both)
        past = datetime.now() - timedelta(seconds=60)
        for s in sessions:
            c._last_update[s.id] = past
            c._last_context_update[s.id] = past

    calls = c._client.summarize.call_count
    # today: 100 (one short + one context per agent, first pass only);
    # before: 2 per agent per pass = 1,200 over the minute at these limits
    assert calls <= 2 * AGENTS, f"{calls} API calls"
    assert fleet.listings == PASSES
