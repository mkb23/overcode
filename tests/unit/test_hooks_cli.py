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


class TestHooksUninstallBackendOpencode2:

    def test_requires_dir_flag(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2"])
        assert result.exit_code != 0
        assert "--dir is required" in result.output

    def test_removes_marked_plugin(self, tmp_path):
        from overcode.backends.opencode2_plugin_install import (
            ensure_plugin_installed,
            project_plugin_dir_v2,
        )

        ensure_plugin_installed(str(tmp_path))
        installed = project_plugin_dir_v2(str(tmp_path))
        assert installed.exists()

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not installed.exists()

    def test_missing_plugin_is_a_clean_no_op(self, tmp_path):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No opencode2 telemetry plugin found" in result.output

    def test_refuses_unmarked_file(self, tmp_path):
        from overcode.backends.opencode2_plugin_install import project_plugin_dir_v2

        installed = project_plugin_dir_v2(str(tmp_path))
        installed.mkdir(parents=True)
        (installed / "index.js").write_text("export const Mine = async () => ({})\n")

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)])
        assert result.exit_code != 0
        assert "not overcode-managed" in " ".join(result.output.split())
        assert (installed / "index.js").exists()

    def test_refuses_when_any_file_is_user_owned(self, tmp_path):
        from overcode.backends.opencode2_plugin_install import (
            ensure_plugin_installed,
            project_plugin_dir_v2,
        )

        ensure_plugin_installed(str(tmp_path))
        installed = project_plugin_dir_v2(str(tmp_path))
        # The user replaced one of the three plugin files with their own.
        (installed / "tui.js").write_text("export const Mine = async () => ({})\n")

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)])
        assert result.exit_code != 0
        assert "not overcode-managed" in " ".join(result.output.split())
        for name in ("index.js", "tui.js", "overcode-telemetry-core.mjs"):
            assert (installed / name).exists()

    def test_unreadable_file_is_skipped_and_others_removed(self, tmp_path):
        # Present-but-unverifiable: an unreadable file (e.g. 0o000 in a
        # writable directory) is NOT absent — only FileNotFoundError
        # counts as missing — so it is skipped with a warning, never
        # deleted, while the readable marked files are removed and the
        # command still succeeds.
        import os as _os

        from overcode.backends.opencode2_plugin_install import (
            ensure_plugin_installed,
            project_plugin_dir_v2,
        )

        ensure_plugin_installed(str(tmp_path))
        installed = project_plugin_dir_v2(str(tmp_path))
        blocked = installed / "tui.js"
        blocked_content = blocked.read_text()
        blocked.chmod(0o000)
        try:
            if _os.access(blocked, _os.R_OK):
                pytest.skip("platform cannot produce an unreadable file (e.g. root)")
            result = runner.invoke(
                app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)]
            )
            assert result.exit_code == 0
            assert "could not be read" in " ".join(result.output.split())
            # Untouched — still present; its content is checked after the
            # perms are restored below.
            assert blocked.is_file()
            # The readable marked files were removed around it.
            assert not (installed / "index.js").exists()
            assert not (installed / "overcode-telemetry-core.mjs").exists()
        finally:
            blocked.chmod(0o644)
        assert blocked.read_text() == blocked_content

    def test_leaves_plugin_dir_when_user_parked_a_file_in_it(self, tmp_path):
        from overcode.backends.opencode2_plugin_install import (
            ensure_plugin_installed,
            project_plugin_dir_v2,
        )

        ensure_plugin_installed(str(tmp_path))
        installed = project_plugin_dir_v2(str(tmp_path))
        (installed / "my-own.js").write_text("// mine\n")

        result = runner.invoke(app, ["hooks", "uninstall-backend", "opencode2", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        for name in ("index.js", "tui.js", "overcode-telemetry-core.mjs"):
            assert not (installed / name).exists()
        assert (installed / "my-own.js").exists()
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


class TestHooksUninstallBackendHermes:

    def test_removes_marked_plugin_dir(self, tmp_path, monkeypatch):
        from overcode.backends.hermes import ensure_plugin_installed, plugin_dir
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        assert ensure_plugin_installed() is not None
        assert (plugin_dir() / "plugin.yaml").exists()

        result = runner.invoke(app, ["hooks", "uninstall-backend", "hermes"])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not plugin_dir().exists()

    def test_missing_plugin_is_a_clean_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        result = runner.invoke(app, ["hooks", "uninstall-backend", "hermes"])
        assert result.exit_code == 0
        assert "No hermes plugin found" in result.output

    def test_refuses_unmarked_plugin(self, tmp_path, monkeypatch):
        from overcode.backends.hermes import plugin_dir
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

        target = plugin_dir()
        target.mkdir(parents=True)
        (target / "plugin.yaml").write_text("name: overcode\nversion: 9\n")

        result = runner.invoke(app, ["hooks", "uninstall-backend", "hermes"])
        assert result.exit_code != 0
        assert "not overcode-managed" in " ".join(result.output.split())
        assert (target / "plugin.yaml").exists()


class TestHooksUninstallBackendUnknown:

    def test_unknown_backend_errors(self):
        result = runner.invoke(app, ["hooks", "uninstall-backend", "no-such-cli"])
        assert result.exit_code != 0
        assert "unknown backend" in result.output
