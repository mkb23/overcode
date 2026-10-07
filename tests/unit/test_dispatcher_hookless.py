"""A hooks-mode agent with no hook state is read from its pane (0.6.0).

The opencode parity audit found a plugin-less agent in a hooks fleet showing
red while it worked: the hook detector answered "waiting for first hook
event" without reading the pane. The dispatcher now polls such an agent
until its first hook state appears.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from overcode.status_detector_factory import StatusDetectorDispatcher

pytestmark = pytest.mark.unit


def _dispatcher(tmp_path):
    polling = MagicMock()
    polling.detect_status.return_value = ("running", "polled", "pane")
    hooks = MagicMock()
    hooks.detect_status.return_value = ("waiting_user", "hooked", "pane")
    hooks._hook_state_path = lambda name: tmp_path / f"hook_state_{name}.json"
    d = StatusDetectorDispatcher("agents", polling_detector=polling, hook_detector=hooks,
                                 mode="hooks")
    return d, polling, hooks


def _session():
    return SimpleNamespace(id="s", name="a", backend="claude-code", tmux_window="a",
                           detection_mode_override=None, hook_status_detection=True)


def test_no_hook_state_reads_the_pane(tmp_path):
    d, polling, hooks = _dispatcher(tmp_path)
    assert d.detect_status(_session())[1] == "polled"
    hooks.detect_status.assert_not_called()


def test_hook_state_present_uses_the_hooks(tmp_path):
    d, polling, hooks = _dispatcher(tmp_path)
    (tmp_path / "hook_state_a.json").write_text("{}")
    assert d.detect_status(_session())[1] == "hooked"
    polling.detect_status.assert_not_called()


def test_polling_mode_is_unchanged(tmp_path):
    d, polling, hooks = _dispatcher(tmp_path)
    d.mode = "polling"
    (tmp_path / "hook_state_a.json").write_text("{}")
    assert d.detect_status(_session())[1] == "polled"
