"""
Tests for tmux toggle-key config (#442).

Covers:
- TOGGLE_KEY_CHOICES includes § and backtick alongside the originals
- change_toggle_key saves config and reinstalls bindings when they were present
- change_toggle_key saves config but skips install when no bindings present
- `overcode config tmux` command runs the picker
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.cli import app
from overcode.cli.split import (
    TOGGLE_KEY_CHOICES,
    DEFAULT_TOGGLE_KEY,
    change_toggle_key,
)


runner = CliRunner()


class TestToggleKeyChoices:
    def test_default_is_tab(self):
        assert DEFAULT_TOGGLE_KEY == "Tab"

    def test_includes_section_sign(self):
        keys = [k for _, k in TOGGLE_KEY_CHOICES]
        assert "§" in keys

    def test_includes_backtick(self):
        keys = [k for _, k in TOGGLE_KEY_CHOICES]
        assert "`" in keys

    def test_preserves_original_choices(self):
        keys = [k for _, k in TOGGLE_KEY_CHOICES]
        assert "Tab" in keys
        assert "C-]" in keys
        assert "C-Space" in keys


class TestChangeToggleKey:
    def test_saves_config_when_no_bindings(self):
        """No tmux bindings installed → save config, skip install."""
        with patch("overcode.cli.split._are_keybindings_installed", return_value=False), \
             patch("overcode.cli.split._remove_keybindings") as mock_remove, \
             patch("overcode.cli.split._setup_keybindings") as mock_setup, \
             patch("overcode.config.set_tmux_toggle_key") as mock_set:
            reinstalled = change_toggle_key("§")

        assert reinstalled is False
        mock_set.assert_called_once_with("§")
        mock_remove.assert_not_called()
        mock_setup.assert_not_called()

    def test_reinstalls_when_bindings_present(self):
        """Bindings installed → remove old, save new, install new."""
        with patch("overcode.cli.split._are_keybindings_installed", return_value=True), \
             patch("overcode.cli.split._remove_keybindings") as mock_remove, \
             patch("overcode.cli.split._setup_keybindings") as mock_setup, \
             patch("overcode.config.set_tmux_toggle_key") as mock_set:
            reinstalled = change_toggle_key("§", linked_session="linked")

        assert reinstalled is True
        mock_remove.assert_called_once()
        mock_set.assert_called_once_with("§")
        mock_setup.assert_called_once_with(linked_session="linked", toggle_key="§")

    def test_remove_called_before_set_and_setup(self):
        """Old bindings must be removed BEFORE config changes, so _remove reads the old key."""
        call_order = []
        with patch("overcode.cli.split._are_keybindings_installed", return_value=True), \
             patch("overcode.cli.split._remove_keybindings",
                   side_effect=lambda: call_order.append("remove")), \
             patch("overcode.config.set_tmux_toggle_key",
                   side_effect=lambda k: call_order.append("set")), \
             patch("overcode.cli.split._setup_keybindings",
                   side_effect=lambda **kw: call_order.append("setup")):
            change_toggle_key("`")

        assert call_order == ["remove", "set", "setup"]


class TestConfigTmuxCommand:
    def test_config_tmux_help(self):
        result = runner.invoke(app, ["config", "tmux", "--help"])
        assert result.exit_code == 0
        assert "toggle" in result.stdout.lower() or "tmux" in result.stdout.lower()

    def test_config_tmux_invokes_picker(self):
        """`overcode config tmux` should call run_toggle_key_picker."""
        with patch("overcode.cli.split.run_toggle_key_picker") as mock_picker, \
             patch("overcode.config.get_tmux_toggle_key", return_value="Tab"):
            result = runner.invoke(app, ["config", "tmux"])
        assert result.exit_code == 0
        mock_picker.assert_called_once()
        # current_key passed through
        _, kwargs = mock_picker.call_args
        args, _ = mock_picker.call_args
        # Accept either positional or kwarg
        if args:
            assert args[0] == "Tab"
        else:
            assert kwargs.get("current_key") == "Tab"
