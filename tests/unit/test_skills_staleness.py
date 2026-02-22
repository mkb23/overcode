"""
Unit tests for skill staleness detection (#290).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

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

    def test_partial_install_stale(self, tmp_path, monkeypatch):
        """If only one skill is outdated, returns True."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        base = tmp_path / ".claude" / "skills"
        names = list(OVERCODE_SKILLS.keys())

        # Install first skill correctly, second with stale content
        for i, name in enumerate(names):
            skill_dir = base / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            content = OVERCODE_SKILLS[name]["content"]
            if i > 0:
                content += "\n# outdated"
            (skill_dir / "SKILL.md").write_text(content)

        assert any_skills_stale() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
