"""Every tmux client honours ``TmuxInterface``.

Code is written against the protocol and tested against the doubles, so a
double that drifts from the protocol (a renamed parameter, a missing
method, a lost default) lets a test pass for a call the real client would
reject. These tests pin each implementation to the protocol's signatures;
what the doubles *do* is exercised by the tests that use them
(test_tmux_manager and the daemon tests).
"""

import inspect

import pytest

from overcode.implementations import RealTmux
from overcode.monitor_daemon import NullTmux
from overcode.protocols import TmuxInterface
from tests.tmux_doubles import MockTmux

IMPLEMENTATIONS = [RealTmux, MockTmux, NullTmux]

PROTOCOL_METHODS = sorted(
    name for name, value in vars(TmuxInterface).items()
    if callable(value) and not name.startswith("_")
)


def _shape(func):
    """Parameter names, kinds and defaults: what a caller depends on."""
    return [(p.name, p.kind, p.default)
            for p in inspect.signature(func).parameters.values()]


def test_the_protocol_is_not_empty():
    assert "capture_pane" in PROTOCOL_METHODS
    assert "list_panes" in PROTOCOL_METHODS


@pytest.mark.parametrize("cls", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_is_a_tmux_interface(cls):
    assert isinstance(cls(), TmuxInterface)


@pytest.mark.parametrize("method", PROTOCOL_METHODS)
@pytest.mark.parametrize("cls", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_signature_matches_the_protocol(cls, method):
    assert _shape(getattr(cls, method)) == _shape(getattr(TmuxInterface, method))


def test_the_test_daemons_null_tmux_has_no_sessions():
    """``MonitorDaemon.for_test`` relies on reads being empty, writes failing."""
    tmux = NullTmux()
    assert tmux.has_session("agents") is False
    assert tmux.list_windows("agents") == []
    assert tmux.list_panes("agents") is None
    assert tmux.capture_pane("agents", "w") is None
    assert tmux.new_window("agents", "w") is None
    assert tmux.send_keys("agents", "w", "x") is False
