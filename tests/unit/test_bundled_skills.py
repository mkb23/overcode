"""The skills overcode installs (overcode skills install)."""

import yaml

from overcode.bundled_skills import DEPRECATED_SKILL_NAMES, OVERCODE_SKILLS


def _frontmatter(content: str) -> dict:
    assert content.startswith("---\n")
    return yaml.safe_load(content.split("---", 2)[1])


def test_frontmatter_parses_and_name_matches_folder():
    # Claude Code names a skill after its folder, opencode after `name:`;
    # when they differ, the two CLIs disagree about what the skill is called.
    for folder, skill in OVERCODE_SKILLS.items():
        meta = _frontmatter(skill["content"])
        assert meta["name"] == folder
        assert meta["description"]


def test_delegation_skill_is_merged_and_retired():
    assert list(OVERCODE_SKILLS) == ["overcode"]
    assert "delegating-to-agents" in DEPRECATED_SKILL_NAMES


def test_skill_teaches_the_report_contract_and_points_at_live_help():
    content = OVERCODE_SKILLS["overcode"]["content"]
    assert "overcode report --status success" in content
    assert "overcode docs path" in content
    assert "--claude-arg" not in content  # deprecated alias
