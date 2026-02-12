"""Tests for the skills CLI commands (install, uninstall, status)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.bundled_skills import OVERCODE_SKILLS


runner = CliRunner()


class TestSkillsInstall:

    def test_installs_all_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert "installed" in result.output

        for name, skill in OVERCODE_SKILLS.items():
            f = tmp_path / ".claude" / "skills" / name / "SKILL.md"
            assert f.exists(), f"SKILL.md not created for {name}"
            assert f.read_text() == skill["content"]

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        runner.invoke(app, ["skills", "install"])
        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert "up-to-date" in result.output

    def test_updates_changed_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        # Install first
        runner.invoke(app, ["skills", "install"])

        # Modify one skill to simulate old version
        skill_file = tmp_path / ".claude" / "skills" / "overcode" / "SKILL.md"
        skill_file.write_text("old content")

        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert "updated" in result.output
        assert skill_file.read_text() == OVERCODE_SKILLS["overcode"]["content"]

    def test_project_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["skills", "install", "--project"])
        assert result.exit_code == 0
        assert "project" in result.output

        for name in OVERCODE_SKILLS:
            f = tmp_path / ".claude" / "skills" / name / "SKILL.md"
            assert f.exists()

    def test_creates_directories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        # Ensure nothing exists
        assert not (tmp_path / ".claude").exists()

        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0

        for name in OVERCODE_SKILLS:
            assert (tmp_path / ".claude" / "skills" / name).is_dir()


class TestSkillsUninstall:

    def test_removes_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        runner.invoke(app, ["skills", "install"])
        result = runner.invoke(app, ["skills", "uninstall"])
        assert result.exit_code == 0
        assert "Removed" in result.output

        for name in OVERCODE_SKILLS:
            assert not (tmp_path / ".claude" / "skills" / name).exists()

    def test_handles_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = runner.invoke(app, ["skills", "uninstall"])
        assert result.exit_code == 0
        assert "No overcode skills found" in result.output

    def test_doesnt_remove_modified(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        runner.invoke(app, ["skills", "install"])

        # Modify one skill
        skill_file = tmp_path / ".claude" / "skills" / "overcode" / "SKILL.md"
        skill_file.write_text("user customized content")

        result = runner.invoke(app, ["skills", "uninstall"])
        assert result.exit_code == 0
        assert "Skipped" in result.output
        # Modified skill directory should still exist
        assert skill_file.exists()
        # Unmodified skill should be removed
        assert not (tmp_path / ".claude" / "skills" / "delegation").exists()

    def test_project_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["skills", "install", "--project"])
        result = runner.invoke(app, ["skills", "uninstall", "--project"])
        assert result.exit_code == 0
        assert "Removed" in result.output


class TestSkillsStatus:

    def test_shows_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["skills", "install"])

        result = runner.invoke(app, ["skills", "status"])
        assert result.exit_code == 0
        assert "installed" in result.output
        for name in OVERCODE_SKILLS:
            assert name in result.output

    def test_shows_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["skills", "status"])
        assert result.exit_code == 0
        assert "not installed" in result.output

    def test_shows_modified(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["skills", "install"])

        # Modify one
        skill_file = tmp_path / ".claude" / "skills" / "overcode" / "SKILL.md"
        skill_file.write_text("modified content")

        result = runner.invoke(app, ["skills", "status"])
        assert result.exit_code == 0
        assert "modified" in result.output

    def test_shows_both_levels(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["skills", "status"])
        assert result.exit_code == 0
        assert "User-level" in result.output
        assert "Project-level" in result.output
