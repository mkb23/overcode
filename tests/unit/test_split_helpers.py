"""Tests for pure helpers in overcode.cli.split.

The full `overcode tmux` command reaches deep into tmux state and can only
be exercised in an integration test. These tests target the helpers that
can be unit-tested without a live tmux server: the setup lock, the toggle-
key picker, the linked-session name derivation, the overcode-bin discovery,
the split-window parsers, and the resize-ratio cycle.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overcode.cli import split as split_mod


class TestAcquireSetupLock:

    def test_fresh_acquire(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert split_mod._acquire_setup_lock() is True
        lock_path = tmp_path / ".overcode" / "tmux-setup.lock"
        assert lock_path.exists()
        # Holder PID recorded so stale-lock checks can probe it.
        assert lock_path.read_text().strip() == str(os.getpid())

    def test_second_acquire_blocked_when_holder_alive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        split_mod._acquire_setup_lock()
        # Current process is alive → second acquire should refuse.
        assert split_mod._acquire_setup_lock() is False

    def test_stale_lock_is_reclaimed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        lock_path = tmp_path / ".overcode" / "tmux-setup.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # PID 1 exists but we'll force-trigger the stale path by writing a
        # clearly-dead PID (ridiculously high).
        lock_path.write_text("99999999")

        # Use a PID that os.kill(pid, 0) treats as dead — mock os.kill for that.
        def fake_kill(pid, sig):
            raise ProcessLookupError()
        monkeypatch.setattr(split_mod.os, "kill", fake_kill)

        assert split_mod._acquire_setup_lock() is True
        assert lock_path.read_text().strip() == str(os.getpid())

    def test_release_removes_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        split_mod._acquire_setup_lock()
        split_mod._release_setup_lock()
        assert not (tmp_path / ".overcode" / "tmux-setup.lock").exists()

    def test_release_idempotent_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        # No lock to begin with — release should not raise.
        split_mod._release_setup_lock()


class TestLinkedSessionName:

    def test_prefix_and_session(self):
        assert split_mod._linked_session_name("agents") == "oc-view-agents"
        assert split_mod._linked_session_name("my-proj") == "oc-view-my-proj"


class TestFindOvercodeCmd:

    def test_prefers_local_venv_bin(self, tmp_path, monkeypatch):
        # Build a fake file layout where cli/split.py lives inside a venv-like
        # tree: .../venv/lib/python/site-packages/overcode/cli/split.py with
        # .../venv/bin/overcode alongside.
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "overcode").write_text("#!/bin/sh\n")
        (venv / "bin" / "overcode").chmod(0o755)

        fake_file = venv / "lib" / "python" / "site-packages" / "overcode" / "cli" / "split.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("# fake module\n")

        monkeypatch.setattr(split_mod, "__file__", str(fake_file))
        result = split_mod._find_overcode_cmd()
        assert result == str(venv / "bin" / "overcode")

    def test_falls_back_to_path(self, tmp_path, monkeypatch):
        # No nearby bin/overcode — ensure fallback goes through shutil.which.
        fake_file = tmp_path / "nowhere" / "overcode" / "cli" / "split.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("")
        monkeypatch.setattr(split_mod, "__file__", str(fake_file))

        with patch("shutil.which", return_value="/usr/local/bin/overcode"):
            assert split_mod._find_overcode_cmd() == "/usr/local/bin/overcode"

        with patch("shutil.which", return_value=None):
            # Last-resort literal — acceptable since tmux will then search PATH.
            assert split_mod._find_overcode_cmd() == "overcode"


class TestToggleKeyPicker:

    @pytest.fixture(autouse=True)
    def _stub_change_toggle_key(self, monkeypatch):
        # Isolate from real config writes.
        monkeypatch.setattr(split_mod, "change_toggle_key", lambda key: False)

    def test_default_on_empty_input(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        assert chosen == "Tab"

    def test_numeric_choice(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        # Index 2 corresponds to C-] in TOGGLE_KEY_CHOICES.
        assert chosen == "C-]"

    def test_invalid_input_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "banana")
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        assert chosen == "Tab"

    def test_out_of_range_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "99")
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        assert chosen == "Tab"

    def test_eof_returns_none(self, monkeypatch):
        def _raise(_):
            raise EOFError()
        monkeypatch.setattr("builtins.input", _raise)
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        assert chosen is None

    def test_keyboard_interrupt_returns_none(self, monkeypatch):
        def _raise(_):
            raise KeyboardInterrupt()
        monkeypatch.setattr("builtins.input", _raise)
        chosen = split_mod.run_toggle_key_picker(current_key=None)
        assert chosen is None

    def test_current_tag_shown(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        split_mod.run_toggle_key_picker(current_key="C-Space")
        out = capsys.readouterr().out
        assert "current" in out


class TestChangeToggleKey:

    def test_saves_config_when_no_bindings(self, monkeypatch):
        saved = {}

        def _set(key):
            saved["key"] = key

        monkeypatch.setattr("overcode.config.set_tmux_toggle_key", _set)
        monkeypatch.setattr(split_mod, "_are_keybindings_installed", lambda: False)
        remove_called = MagicMock()
        setup_called = MagicMock()
        monkeypatch.setattr(split_mod, "_remove_keybindings", remove_called)
        monkeypatch.setattr(split_mod, "_setup_keybindings", setup_called)

        reinstalled = split_mod.change_toggle_key("C-]")

        assert saved["key"] == "C-]"
        assert reinstalled is False
        # Must not touch tmux when nothing was installed.
        remove_called.assert_not_called()
        setup_called.assert_not_called()

    def test_reinstalls_when_bindings_present(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            "overcode.config.set_tmux_toggle_key",
            lambda key: saved.setdefault("key", key),
        )
        monkeypatch.setattr(split_mod, "_are_keybindings_installed", lambda: True)
        remove_called = MagicMock()
        setup_called = MagicMock()
        monkeypatch.setattr(split_mod, "_remove_keybindings", remove_called)
        monkeypatch.setattr(split_mod, "_setup_keybindings", setup_called)

        reinstalled = split_mod.change_toggle_key("C-Space", linked_session="oc-view-agents")

        assert reinstalled is True
        assert saved["key"] == "C-Space"
        remove_called.assert_called_once()
        setup_called.assert_called_once_with(
            linked_session="oc-view-agents", toggle_key="C-Space",
        )


class TestSplitWindowParsers:
    """Exercise the helpers that parse `tmux list-windows`-style output."""

    @staticmethod
    def _cp(stdout="", returncode=0):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)

    def test_find_existing_split_window_hit(self, monkeypatch):
        # Includes a decoy "overc-tmux" window — must only match the exact
        # SPLIT_WINDOW_NAME ("overcode-tmux") to avoid substring false positives.
        out = "other-window @1\noverc-tmux @99\novercode-tmux @42\n"
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp(out),
        )
        assert split_mod._find_existing_split_window("agents") == "@42"

    def test_find_existing_split_window_miss(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp("main @1\nother @2\n"),
        )
        assert split_mod._find_existing_split_window("agents") is None

    def test_find_existing_split_window_tmux_failed(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp("", returncode=1),
        )
        assert split_mod._find_existing_split_window("agents") is None

    def test_find_any_session_hit(self, monkeypatch):
        out = "work main @1\nwork overcode-tmux @7\nagents overcode-tmux @12\n"
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp(out),
        )
        # First match wins; order is whatever tmux returned.
        result = split_mod._find_existing_split_window_any_session()
        assert result == ("work", "@7")

    def test_find_any_session_none(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp("work main @1\n"),
        )
        assert split_mod._find_existing_split_window_any_session() is None

    def test_get_first_agent_window(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp("alpha\nbeta\n"),
        )
        assert split_mod._get_first_agent_window("agents") == "alpha"

    def test_get_first_agent_window_empty(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestSplitWindowParsers._cp(""),
        )
        assert split_mod._get_first_agent_window("agents") is None


class TestIsSplitWindowHealthy:

    @staticmethod
    def _cp(stdout="", returncode=0):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)

    def test_two_panes_monitor_running(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestIsSplitWindowHealthy._cp("python\nzsh\n"),
        )
        assert split_mod._is_split_window_healthy("@42") is True

    def test_top_pane_is_bare_shell_is_unhealthy(self, monkeypatch):
        # If monitor died, top pane drops back to zsh — this is the signal
        # that the pane needs to be rebuilt.
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestIsSplitWindowHealthy._cp("zsh\nzsh\n"),
        )
        assert split_mod._is_split_window_healthy("@42") is False

    def test_wrong_pane_count(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestIsSplitWindowHealthy._cp("python\n"),
        )
        assert split_mod._is_split_window_healthy("@42") is False

    def test_tmux_failure(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: TestIsSplitWindowHealthy._cp("", returncode=1),
        )
        assert split_mod._is_split_window_healthy("@42") is False


class TestRatioCycle:
    """Exercise the resize-ratio selection logic inside tmux_resize."""

    @staticmethod
    def _cp(stdout="", returncode=0):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)

    def _invoke_with_ratio(self, monkeypatch, current_pct, win_h=100):
        """Run tmux_resize and capture the `-y` argument passed to resize-pane."""
        calls = []
        pane_h = current_pct  # win_h = 100 makes these directly equivalent

        def fake_tmux(*args, **kwargs):
            if args and args[0] == "display-message":
                return TestRatioCycle._cp(f"{pane_h}:{win_h}")
            calls.append(args)
            return TestRatioCycle._cp()

        monkeypatch.setattr(split_mod, "_tmux", fake_tmux)
        monkeypatch.setattr(
            split_mod, "_tmux_output",
            lambda *a, **kw: f"{pane_h}:{win_h}",
        )
        monkeypatch.setattr(split_mod, "get_pane_base_index", lambda: 0)

        split_mod.tmux_resize()

        for call in calls:
            if call and call[0] == "resize-pane":
                # Structure: ("resize-pane", "-t", target, "-y", "<height>")
                return int(call[-1])
        return None

    def test_cycle_25_to_33(self, monkeypatch):
        assert self._invoke_with_ratio(monkeypatch, 25) == 33

    def test_cycle_33_to_50(self, monkeypatch):
        assert self._invoke_with_ratio(monkeypatch, 33) == 50

    def test_cycle_50_wraps_to_25(self, monkeypatch):
        assert self._invoke_with_ratio(monkeypatch, 50) == 25

    def test_minimum_height_floor(self, monkeypatch):
        # win_h small enough that 25% rounds below 5 should floor at 5.
        applied = self._invoke_with_ratio(monkeypatch, 33, win_h=10)
        assert applied is not None
        assert applied >= 5

    def test_malformed_info_is_noop(self, monkeypatch):
        calls = []

        def fake_tmux(*args, **kwargs):
            calls.append(args)
            return TestRatioCycle._cp()

        monkeypatch.setattr(split_mod, "_tmux", fake_tmux)
        monkeypatch.setattr(split_mod, "_tmux_output", lambda *a, **kw: "")
        split_mod.tmux_resize()
        # Should not have called resize-pane.
        assert not any(c and c[0] == "resize-pane" for c in calls)

    def test_zero_window_height_is_noop(self, monkeypatch):
        calls = []

        def fake_tmux(*args, **kwargs):
            calls.append(args)
            return TestRatioCycle._cp()

        monkeypatch.setattr(split_mod, "_tmux", fake_tmux)
        monkeypatch.setattr(split_mod, "_tmux_output", lambda *a, **kw: "10:0")
        split_mod.tmux_resize()
        assert not any(c and c[0] == "resize-pane" for c in calls)


class TestAreKeybindingsInstalled:

    def test_sentinel_present(self, monkeypatch):
        out = (
            "bind-key    -T root M-j if-shell -F "
            "'#{...}' 'send-keys -t overcode:overcode-tmux.0 j' ...\n"
        )
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[], returncode=0, stdout=out,
            ),
        )
        assert split_mod._are_keybindings_installed() is True

    def test_sentinel_absent(self, monkeypatch):
        out = "bind-key -T root C-b send-prefix\n"
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[], returncode=0, stdout=out,
            ),
        )
        assert split_mod._are_keybindings_installed() is False

    def test_tmux_failure(self, monkeypatch):
        monkeypatch.setattr(
            split_mod, "_tmux",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[], returncode=1, stdout="",
            ),
        )
        assert split_mod._are_keybindings_installed() is False
