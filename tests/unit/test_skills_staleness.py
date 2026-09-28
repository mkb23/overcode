"""
Unit tests for skill staleness detection (#290).
"""

from pathlib import Path

import pytest

from overcode.bundled_skills import any_skills_stale, OVERCODE_SKILLS


class TestAnySkillsStale:
    """Test any_skills_stale() detection."""

    def test_not_installed_is_not_stale(self, tmp_path, monkeypatch):
        """Skills that aren't installed are not considered stale."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert any_skills_stale() is False

    def test_current_skills_not_stale(self, tmp_path, monkeypatch):
        """Skills matching bundled content are not stale."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        base = tmp_path / ".claude" / "skills"
        for name, skill in OVERCODE_SKILLS.items():
            skill_dir = base / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill["content"])

        assert any_skills_stale() is False

    def test_outdated_skill_is_stale(self, tmp_path, monkeypatch):
        """Skills with different content than bundled are stale."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        base = tmp_path / ".claude" / "skills"
        for name, skill in OVERCODE_SKILLS.items():
            skill_dir = base / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill["content"] + "\n# old stuff")

        assert any_skills_stale() is True

    def test_retired_skill_still_installed_is_stale(self, tmp_path, monkeypatch):
        """Current skills up to date, but a merged-away skill is still installed."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        base = tmp_path / ".claude" / "skills"
        for name, skill in OVERCODE_SKILLS.items():
            (base / name).mkdir(parents=True)
            (base / name / "SKILL.md").write_text(skill["content"])
        assert any_skills_stale() is False

        (base / "delegating-to-agents").mkdir()
        (base / "delegating-to-agents" / "SKILL.md").write_text("old")
        assert any_skills_stale() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
