"""Tests for the hooks CLI commands (install [deprecated], uninstall, status)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.hook_handler import OVERCODE_HOOKS


runner = CliRunner()


def _write_settings_with_hooks(settings_path: Path) -> None:
    """Manually write a settings.json with all overcode hooks installed.

    Used by uninstall/status tests since the install command is deprecated.
    """
    hooks = {}
    for event, command in OVERCODE_HOOKS:
        hooks.setdefault(event, []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": command}],
        })
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"hooks": hooks}))


class TestHooksInstall:
    """Install is deprecated — just prints a notice."""

    def test_shows_deprecation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        assert "deprecated" in result.output.lower()

    def test_does_not_write_settings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        runner.invoke(app, ["hooks", "install"])
        f = tmp_path / ".claude" / "settings.json"
        assert not f.exists()


class TestHooksUninstall:

    def test_uninstalls_all_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        _write_settings_with_hooks(tmp_path / ".claude" / "settings.json")

        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 0
        assert "Removed" in result.output

        # Verify hooks are gone
        f = tmp_path / ".claude" / "settings.json"
        data = json.loads(f.read_text())
        assert "hooks" not in data

    def test_uninstall_no_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 0
        assert "No overcode hooks found" in result.output

    def test_uninstall_project_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_settings_with_hooks(tmp_path / ".claude" / "settings.json")

        result = runner.invoke(app, ["hooks", "uninstall", "--project"])
        assert result.exit_code == 0
        assert "Removed" in result.output


class TestHooksStatus:

    def test_shows_installed_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        _write_settings_with_hooks(tmp_path / ".claude" / "settings.json")

        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "UserPromptSubmit" in result.output
        assert "PostToolUse" in result.output
        assert "Stop" in result.output
        assert "PermissionRequest" in result.output
        assert "SessionEnd" in result.output

    def test_shows_deprecation_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "deprecated" in result.output.lower()

    def test_shows_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        # Create settings file so it doesn't short-circuit with "no settings file"
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "not installed" in result.output

class TestHookHandlerCommand:

    def test_help(self):
        # hidden=True still allows --help
        result = runner.invoke(app, ["hook-handler", "--help"])
        assert result.exit_code == 0
