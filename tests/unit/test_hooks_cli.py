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

class TestHooksUninstallBackendClaudeAndCodex:
    """claude-code and codex install nothing on disk — nothing to remove."""

    def test_claude_code_says_nothing_installed(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "claude-code"])
        assert result.exit_code == 0
        assert "nothing installed on disk" in result.output

    def test_codex_says_nothing_installed(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "codex"])
        assert result.exit_code == 0
        assert "nothing installed on disk" in result.output


class TestHooksUninstallBackendGrok:

    def test_removes_marked_hooks_file(self, tmp_path, monkeypatch):
        from overcode.backends.grok import ensure_hooks_installed, hooks_file_path
        monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))

        ensure_hooks_installed()
        assert hooks_file_path().exists()

        result = runner.invoke(app, ["hooks", "uninstall-backend", "grok"])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not hooks_file_path().exists()

    def test_missing_file_is_a_clean_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))

        result = runner.invoke(app, ["hooks", "uninstall-backend", "grok"])
        assert result.exit_code == 0
        assert "No grok hooks file found" in result.output

    def test_refuses_unmarked_file(self, tmp_path, monkeypatch):
        import json
        from overcode.backends.grok import hooks_file_path
        monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))

        path = hooks_file_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"description": "my own hooks file", "hooks": {}}))

        result = runner.invoke(app, ["hooks", "uninstall-backend", "grok"])
        assert result.exit_code != 0
        assert "not overcode-managed" in " ".join(result.output.split())
        assert path.exists()


class TestHooksUninstallBackendOpencode:

    def test_requires_dir_flag(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode"])
        assert result.exit_code != 0
        assert "--dir is required" in result.output

    def test_removes_marked_plugin(self, tmp_path):
        from overcode.backends.opencode import ensure_plugin_installed, project_plugin_path

        ensure_plugin_installed(str(tmp_path))
        installed = project_plugin_path(str(tmp_path))
        assert installed.exists()

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not installed.exists()

    def test_missing_plugin_is_a_clean_no_op(self, tmp_path):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No opencode telemetry plugin found" in result.output

    def test_refuses_unmarked_file(self, tmp_path):
        from overcode.backends.opencode import project_plugin_path

        installed = project_plugin_path(str(tmp_path))
        installed.parent.mkdir(parents=True)
        installed.write_text("export const Mine = async () => ({})\n")

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode", "--dir", str(tmp_path)])
        assert result.exit_code != 0
        assert "not overcode-managed" in " ".join(result.output.split())
        assert installed.exists()


class TestHooksUninstallBackendUnknown:
    def test_unknown_backend_errors(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "something-else"])
        assert result.exit_code != 0
        assert "unknown backend" in result.output.lower()


class TestHookHandlerCommand:

    def test_help(self):
        # hidden=True still allows --help
        result = runner.invoke(app, ["hook-handler", "--help"])
        assert result.exit_code == 0
