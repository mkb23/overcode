"""The TUI must not leave its tmux pane zoomed when it exits.

Dialogs and the sister view zoom the TUI pane to hide the bottom terminal
pane. If the viewer exits in that state (``q`` from a dialog, a crash), the
zoom survives: the pane holds at the relaunch prompt or is respawned by
``overcode tmux``, and the split shows only the monitor with the integrated
terminal hidden. ``on_unmount`` clears the zoom; ``_unzoom_tui_pane`` is the
shared helper behind dialog close, sister-view exit and that cleanup.
"""

import subprocess
from unittest.mock import MagicMock, patch

from overcode.tui import SupervisorTUI


def _tui(in_split: bool = True) -> MagicMock:
    tui = MagicMock()
    tui.in_split = in_split
    tui._prefs.status_change_logging = False
    tui._tui_pane_target.return_value = "overcode:overcode-tmux.1"
    return tui


class TestUnzoomTuiPane:

    def _run(self, zoomed_flag: str, in_split: bool = True) -> list:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            stdout = f"{zoomed_flag}\n" if "display-message" in cmd else ""
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout)

        with patch("overcode.tui._tmux_base", return_value=["tmux"]), \
             patch("subprocess.run", side_effect=fake_run):
            SupervisorTUI._unzoom_tui_pane(_tui(in_split))
        return calls

    def test_unzooms_when_zoomed(self):
        resizes = [c for c in self._run("1") if "resize-pane" in c]
        assert resizes == [["tmux", "resize-pane", "-t", "overcode:overcode-tmux.1", "-Z"]]

    def test_no_toggle_when_not_zoomed(self):
        # resize-pane -Z toggles; running it on an unzoomed window would hide
        # the terminal pane instead of revealing it.
        assert not [c for c in self._run("0") if "resize-pane" in c]

    def test_outside_the_split_runs_no_tmux(self):
        # An app built without a linked session (unit tests) never touches tmux
        assert self._run("1", in_split=False) == []


class TestOnUnmountUnzooms:

    def test_exit_unzooms(self):
        tui = _tui()
        SupervisorTUI.on_unmount(tui)
        tui._unzoom_tui_pane.assert_called_once_with()

    def test_unzoom_failure_does_not_block_cleanup(self):
        tui = _tui()
        tui._unzoom_tui_pane.side_effect = OSError("no tmux")
        SupervisorTUI.on_unmount(tui)
        tui._cleanup_ssh_proxies.assert_called_once()
        tui._summarizer.stop.assert_called_once()
