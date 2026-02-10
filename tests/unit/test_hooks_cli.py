"""Tests for the hooks CLI commands (install, uninstall, status)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.hook_handler import OVERCODE_HOOKS, LEGACY_HOOKS


runner = CliRunner()


class TestHooksInstall:

    def test_installs_all_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        assert "Installed" in result.output

        f = tmp_path / ".claude" / "settings.json"
        assert f.exists()
        data = json.loads(f.read_text())
        for event, command in OVERCODE_HOOKS:
            found = False
            for entry in data["hooks"].get(event, []):
                for hook in entry.get("hooks", []):
                    if hook.get("command") == command:
                        found = True
            assert found, f"Hook not found for {event}"

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        # Install once
        runner.invoke(app, ["hooks", "install"])
        # Install again
        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        assert "already installed" in result.output

        # Verify no duplicates
        f = tmp_path / ".claude" / "settings.json"
        data = json.loads(f.read_text())
        for event, command in OVERCODE_HOOKS:
            count = 0
            for entry in data["hooks"].get(event, []):
                for hook in entry.get("hooks", []):
                    if hook.get("command") == command:
                        count += 1
            assert count == 1, f"Duplicate hooks for {event}"

    def test_migrates_legacy_hook(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Install legacy hook first
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        assert "Migrated" in result.output

        # Legacy hook should be gone, new hook present
        data = json.loads((claude_dir / "settings.json").read_text())
        for entry in data["hooks"]["UserPromptSubmit"]:
            for hook in entry.get("hooks", []):
                assert hook["command"] != "overcode time-context"
        # New hook should be there
        found = False
        for entry in data["hooks"]["UserPromptSubmit"]:
            for hook in entry.get("hooks", []):
                if hook["command"] == "overcode hook-handler":
                    found = True
        assert found

    def test_project_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["hooks", "install", "--project"])
        assert result.exit_code == 0
        assert "project" in result.output
        f = tmp_path / ".claude" / "settings.json"
        assert f.exists()

    def test_preserves_existing_settings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"existingKey": True}))

        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        data = json.loads((claude_dir / "settings.json").read_text())
        assert data["existingKey"] is True

    def test_invalid_json_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not json{{{")
        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 1
        assert "Error" in result.output


class TestHooksUninstall:

    def test_uninstalls_all_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        # Install first
        runner.invoke(app, ["hooks", "install"])

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

    def test_uninstall_removes_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 0
        assert "Removed" in result.output

        data = json.loads((claude_dir / "settings.json").read_text())
        assert "hooks" not in data

    def test_uninstall_project_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Install project hooks first
        runner.invoke(app, ["hooks", "install", "--project"])
        result = runner.invoke(app, ["hooks", "uninstall", "--project"])
        assert result.exit_code == 0
        assert "Removed" in result.output


class TestHooksStatus:

    def test_shows_installed_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["hooks", "install"])

        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "UserPromptSubmit" in result.output
        assert "PostToolUse" in result.output
        assert "Stop" in result.output
        assert "PermissionRequest" in result.output
        assert "SessionEnd" in result.output

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

    def test_shows_legacy_hook_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "legacy" in result.output
        assert "migrate" in result.output.lower()


class TestHookHandlerCommand:

    def test_help(self):
        # hidden=True still allows --help
        result = runner.invoke(app, ["hook-handler", "--help"])
        assert result.exit_code == 0
