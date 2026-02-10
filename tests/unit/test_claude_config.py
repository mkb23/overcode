"""Tests for ClaudeConfigEditor."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode.claude_config import ClaudeConfigEditor
from overcode.cli import app


runner = CliRunner()


class TestClaudeConfigEditorLoad:

    def test_load_nonexistent_returns_empty(self, tmp_path):
        editor = ClaudeConfigEditor(tmp_path / "missing.json")
        assert editor.load() == {}

    def test_load_valid_json(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text('{"key": "value"}')
        editor = ClaudeConfigEditor(f)
        assert editor.load() == {"key": "value"}

    def test_load_invalid_json_raises(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("not json{{{")
        editor = ClaudeConfigEditor(f)
        with pytest.raises(ValueError, match="Invalid JSON"):
            editor.load()

    def test_load_non_object_raises(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("[1, 2, 3]")
        editor = ClaudeConfigEditor(f)
        with pytest.raises(ValueError, match="non-object"):
            editor.load()


class TestClaudeConfigEditorSave:

    def test_save_creates_dirs_and_writes(self, tmp_path):
        f = tmp_path / "nested" / "dir" / "settings.json"
        editor = ClaudeConfigEditor(f)
        editor.save({"hello": "world"})
        assert f.exists()
        data = json.loads(f.read_text())
        assert data == {"hello": "world"}

    def test_save_uses_indent_and_trailing_newline(self, tmp_path):
        f = tmp_path / "settings.json"
        editor = ClaudeConfigEditor(f)
        editor.save({"a": 1})
        text = f.read_text()
        assert text == '{\n  "a": 1\n}\n'


class TestClaudeConfigEditorClassmethods:

    def test_user_level_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        editor = ClaudeConfigEditor.user_level()
        assert editor.path == tmp_path / ".claude" / "settings.json"

    def test_project_level_default_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        editor = ClaudeConfigEditor.project_level()
        assert editor.path == tmp_path / ".claude" / "settings.json"

    def test_project_level_explicit_dir(self, tmp_path):
        editor = ClaudeConfigEditor.project_level(tmp_path / "myproject")
        assert editor.path == tmp_path / "myproject" / ".claude" / "settings.json"


class TestHasHook:

    def test_empty_settings(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("{}")
        editor = ClaudeConfigEditor(f)
        assert not editor.has_hook("UserPromptSubmit", "overcode time-context")

    def test_no_hooks_key(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text('{"other": "stuff"}')
        editor = ClaudeConfigEditor(f)
        assert not editor.has_hook("UserPromptSubmit", "overcode time-context")

    def test_different_command(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert not editor.has_hook("UserPromptSubmit", "overcode time-context")

    def test_different_event(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert not editor.has_hook("UserPromptSubmit", "overcode time-context")

    def test_found(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.has_hook("UserPromptSubmit", "overcode time-context")


class TestAddHook:

    def test_add_to_empty(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("{}")
        editor = ClaudeConfigEditor(f)
        assert editor.add_hook("UserPromptSubmit", "overcode time-context") is True
        data = json.loads(f.read_text())
        assert len(data["hooks"]["UserPromptSubmit"]) == 1
        entry = data["hooks"]["UserPromptSubmit"][0]
        assert entry["matcher"] == ""
        assert entry["hooks"][0]["command"] == "overcode time-context"

    def test_add_to_nonexistent_file(self, tmp_path):
        f = tmp_path / ".claude" / "settings.json"
        editor = ClaudeConfigEditor(f)
        assert editor.add_hook("UserPromptSubmit", "overcode time-context") is True
        assert f.exists()
        data = json.loads(f.read_text())
        assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "overcode time-context"

    def test_preserves_existing_settings(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text(json.dumps({"alwaysThinkingEnabled": True, "hooks": {
            "Notification": [{"matcher": "x", "hooks": []}]
        }}))
        editor = ClaudeConfigEditor(f)
        editor.add_hook("UserPromptSubmit", "overcode time-context")
        data = json.loads(f.read_text())
        assert data["alwaysThinkingEnabled"] is True
        assert len(data["hooks"]["Notification"]) == 1
        assert len(data["hooks"]["UserPromptSubmit"]) == 1

    def test_appends_to_existing_event(self, tmp_path):
        f = tmp_path / "settings.json"
        existing = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
        ]}}
        f.write_text(json.dumps(existing))
        editor = ClaudeConfigEditor(f)
        editor.add_hook("UserPromptSubmit", "overcode time-context")
        data = json.loads(f.read_text())
        assert len(data["hooks"]["UserPromptSubmit"]) == 2

    def test_returns_false_when_exists(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.add_hook("UserPromptSubmit", "overcode time-context") is False

    def test_custom_matcher(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("{}")
        editor = ClaudeConfigEditor(f)
        editor.add_hook("PreToolUse", "my-cmd", matcher="Bash")
        data = json.loads(f.read_text())
        assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


class TestRemoveHook:

    def test_remove_existing_hook(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.remove_hook("UserPromptSubmit", "overcode hook-handler") is True
        data = json.loads(f.read_text())
        assert "hooks" not in data

    def test_remove_nonexistent_hook(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text('{"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "other"}]}]}}')
        editor = ClaudeConfigEditor(f)
        assert editor.remove_hook("UserPromptSubmit", "overcode hook-handler") is False
        # File unchanged
        data = json.loads(f.read_text())
        assert len(data["hooks"]["Stop"]) == 1

    def test_remove_from_empty(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("{}")
        editor = ClaudeConfigEditor(f)
        assert editor.remove_hook("UserPromptSubmit", "overcode hook-handler") is False

    def test_remove_last_hook_in_event(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"other_key": True, "hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
        ], "Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.remove_hook("UserPromptSubmit", "overcode hook-handler") is True
        data = json.loads(f.read_text())
        assert "UserPromptSubmit" not in data["hooks"]
        assert len(data["hooks"]["Stop"]) == 1
        assert data["other_key"] is True

    def test_remove_last_hook_overall(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"other_key": True, "hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        editor.remove_hook("UserPromptSubmit", "overcode hook-handler")
        data = json.loads(f.read_text())
        assert "hooks" not in data
        assert data["other_key"] is True

    def test_remove_preserves_other_hooks_in_event(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]},
            {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]},
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.remove_hook("UserPromptSubmit", "overcode hook-handler") is True
        data = json.loads(f.read_text())
        assert len(data["hooks"]["UserPromptSubmit"]) == 1
        assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "other-tool"


class TestListHooksMatching:

    def test_finds_overcode_hooks(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
            ],
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]}
            ],
            "PreToolUse": [
                {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
            ],
        }}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        result = editor.list_hooks_matching("overcode")
        assert len(result) == 2
        assert ("UserPromptSubmit", "overcode hook-handler") in result
        assert ("Stop", "overcode hook-handler") in result

    def test_no_matches(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {"Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
        ]}}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        assert editor.list_hooks_matching("overcode") == []

    def test_empty_settings(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text("{}")
        editor = ClaudeConfigEditor(f)
        assert editor.list_hooks_matching("overcode") == []

    def test_matches_legacy_hooks(self, tmp_path):
        f = tmp_path / "settings.json"
        settings = {"hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "overcode time-context"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "overcode hook-handler"}]},
            ],
        }}
        f.write_text(json.dumps(settings))
        editor = ClaudeConfigEditor(f)
        result = editor.list_hooks_matching("overcode")
        assert len(result) == 2
        assert ("UserPromptSubmit", "overcode time-context") in result
        assert ("UserPromptSubmit", "overcode hook-handler") in result
