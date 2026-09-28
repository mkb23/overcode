"""Skill profiles (#499): library scanning, profiles, pins, launch wiring."""

import json
import os
import shlex
from pathlib import Path

import pytest

from overcode import skill_library as sl
from overcode.backends.base import LaunchSpec
from overcode.backends.claude_code import ClaudeCodeBackend
from overcode.backends.opencode import OpencodeBackend
from overcode.launcher import AgentLauncher
from overcode.mocks import MockTmux
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager


def make_skill(parent: Path, folder: str, name: str = None, description: str = "does things"):
    d = parent / folder
    d.mkdir(parents=True)
    front = f"name: {name}\n" if name else ""
    (d / "SKILL.md").write_text(f"---\n{front}description: {description}\n---\n\nBody\n")
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home with its own ~/.overcode; the skills config is in memory
    (conftest's no_real_skill_profiles)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("OVERCODE_DIR", str(h / ".overcode"))
    return h


class TestScan:
    def test_frontmatter_name_and_description(self, home):
        make_skill(home / ".overcode" / "skills", "folder-name", name="real-name",
                   description="Multi\n  line")
        [skill] = sl.scan_library()
        assert skill.name == "real-name"
        assert skill.description == "Multi line"
        assert skill.source == "library"

    def test_folder_name_when_no_frontmatter_name(self, home):
        make_skill(home / ".overcode" / "skills", "plain")
        assert [s.name for s in sl.scan_library()] == ["plain"]

    def test_skips_hidden_and_vendored_dirs_and_stops_at_a_skill(self, home):
        root = home / ".overcode" / "skills"
        make_skill(root / ".git", "hidden")
        make_skill(root / "node_modules", "vendored")
        outer = make_skill(root, "outer")
        make_skill(outer, "inner")
        assert [s.name for s in sl.scan_library()] == ["outer"]

    def test_library_path_in_a_code_root_with_plugin_name(self, home):
        repo = home / "Code" / "team-plugins"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "plugin.json").write_text('{"name": "team"}')
        make_skill(repo / "skills", "review")
        sl.add_library_path("~/Code/team-plugins")
        [skill] = sl.scan_library()
        assert skill.name == "review"
        assert skill.plugin == "team"
        assert skill.source == "~/Code/team-plugins"

    def test_first_root_wins_on_a_name_clash(self, home):
        own = make_skill(home / ".overcode" / "skills", "dup")
        make_skill(home / "repo", "dup")
        sl.add_library_path(str(home / "repo"))
        [skill] = sl.scan_library()
        assert skill.path == own
        assert list(sl.library_duplicates()) == ["dup"]

    def test_personal_skills_claude_reads_one_level_opencode_recurses(self, home):
        personal = home / ".claude" / "skills"
        make_skill(personal, "top")
        make_skill(personal / "synced" / "abc", "deep")
        make_skill(home / ".agents" / "skills", "agents-one")
        assert [s.name for s in sl.personal_skills("claude-code")] == ["top"]
        assert sorted(s.name for s in sl.personal_skills("opencode")) == \
            ["agents-one", "deep", "top"]
        assert sl.personal_skills("codex") == []

    def test_overlapping_library_paths(self, home):
        sl.add_library_path("~/.claude/skills")
        sl.add_library_path("~/Code/skills")
        assert sl.overlapping_library_paths() == ["~/.claude/skills"]


class TestProfilesAndPins:
    def test_profile_crud(self, home):
        sl.save_profile("research", ["a", "b", "a"])
        assert sl.get_profiles() == {"research": ["a", "b"]}
        assert sl.delete_profile("research")
        assert sl.get_profiles() == {}
        assert not sl.delete_profile("research")

    @pytest.mark.parametrize("bad", ["Research", "has space", "none", "", "-x"])
    def test_bad_profile_names(self, home, bad):
        with pytest.raises(ValueError):
            sl.save_profile(bad, [])

    def test_deleting_a_profile_removes_its_pins(self, home):
        sl.save_profile("ios", [])
        sl.pin_folder(str(home / "app"), "ios")
        sl.delete_profile("ios")
        assert sl.get_folder_pins() == {}

    def test_pin_uses_nearest_ancestor_and_not_name_prefixes(self, home):
        sl.pin_folder(str(home / "Code"), "general")
        sl.pin_folder(str(home / "Code" / "app"), "ios")
        assert sl.profile_for_folder(str(home / "Code" / "app" / "src")) == "ios"
        assert sl.profile_for_folder(str(home / "Code" / "other")) == "general"
        assert sl.profile_for_folder(str(home / "Code" / "application")) == "general"
        assert sl.profile_for_folder(str(home / "Elsewhere")) is None

    def test_pins_are_recorded_with_tilde_and_replace_older_pin(self, home):
        assert sl.pin_folder(str(home / "app"), "a") == "~/app"
        sl.pin_folder("~/app", "b")
        assert sl.get_folder_pins() == {"~/app": "b"}
        assert sl.unpin_folder(str(home / "app"))
        assert sl.get_folder_pins() == {}

    def test_resolution_order(self, home):
        sl.pin_folder(str(home / "app"), "pinned")
        d = str(home / "app")
        assert sl.resolve_profile_name("explicit", "parent", d, "default") == "explicit"
        assert sl.resolve_profile_name(None, "parent", d, "default") == "parent"
        assert sl.resolve_profile_name(None, None, d, "default") == "pinned"
        assert sl.resolve_profile_name(None, None, str(home), "default") == "default"
        assert sl.resolve_profile_name("none", "parent", d, "default") is None
        sl.pin_folder(d, "none")
        assert sl.resolve_profile_name(None, None, d, "default") is None


class TestPrepareProfile:
    def test_links_library_skills_and_hides_other_personal_skills(self, home):
        lib = make_skill(home / ".overcode" / "skills", "shirka")
        make_skill(home / ".claude" / "skills", "kept")
        make_skill(home / ".claude" / "skills", "noisy")
        sl.save_profile("research", ["shirka", "kept", "ghost"])

        prepared = sl.prepare_profile("research", "claude-code")
        assert prepared.hidden == ["noisy"]
        assert prepared.missing == ["ghost"]
        root = Path(prepared.skill_dir)
        assert json.loads((root / ".claude-plugin" / "plugin.json").read_text())["name"] == "research"
        links = sorted(p.name for p in (root / "skills").iterdir())
        assert links == ["shirka"]  # "kept" is already visible, so not linked
        assert os.path.realpath(root / "skills" / "shirka") == os.path.realpath(lib)

    def test_rebuild_drops_links_no_longer_in_the_profile(self, home):
        make_skill(home / ".overcode" / "skills", "a")
        make_skill(home / ".overcode" / "skills", "b")
        sl.save_profile("p", ["a", "b"])
        sl.prepare_profile("p", "opencode")
        sl.save_profile("p", ["b"])
        root = Path(sl.prepare_profile("p", "opencode").skill_dir)
        assert [p.name for p in (root / "skills").iterdir()] == ["b"]

    def test_nothing_to_link_means_no_skill_dir(self, home):
        make_skill(home / ".claude" / "skills", "x")
        sl.save_profile("empty", [])
        prepared = sl.prepare_profile("empty", "claude-code")
        assert prepared.skill_dir is None
        assert prepared.hidden == ["x"]

    def test_unknown_profile_or_backend(self, home):
        sl.save_profile("p", [])
        assert sl.prepare_profile("missing", "claude-code") is None
        assert sl.prepare_profile("p", "codex") is None


class TestBackends:
    def test_claude_plugin_dir_and_skill_overrides(self):
        cmd = ClaudeCodeBackend().build_command(
            LaunchSpec(skill_dir="/p/research", hidden_skills=["noisy", "other"]))
        assert cmd[cmd.index("--plugin-dir") + 1] == "/p/research"
        settings = json.loads(cmd[cmd.index("--settings") + 1])
        assert settings["skillOverrides"] == {"noisy": "off", "other": "off"}

    def test_claude_without_profile_is_unchanged(self):
        cmd = ClaudeCodeBackend().build_command(LaunchSpec())
        assert "--plugin-dir" not in cmd
        assert "skillOverrides" not in json.loads(cmd[cmd.index("--settings") + 1])

    def _env(self, **kw):
        env = OpencodeBackend().env_prefix(LaunchSpec(**kw))
        return {k: shlex.split(v)[0] for k, v in env.items()}

    def test_opencode_config_dir_and_deny_rules(self):
        env = self._env(skill_dir="/p/research", hidden_skills=["noisy"])
        assert env["OPENCODE_CONFIG_DIR"] == "/p/research"
        assert json.loads(env["OPENCODE_PERMISSION"]) == {"skill": {"noisy": "deny"}}

    def test_opencode_bypass_keeps_allow_everything_for_other_skills(self):
        env = self._env(permissiveness_mode="bypass", hidden_skills=["noisy"])
        perm = json.loads(env["OPENCODE_PERMISSION"])
        assert perm["skill"] == {"*": "allow", "noisy": "deny"}
        assert perm["bash"] == "allow"

    def test_opencode_without_profile_is_unchanged(self):
        assert "OPENCODE_PERMISSION" not in self._env()
        assert "OPENCODE_CONFIG_DIR" not in self._env()


class TestLauncher:
    @pytest.fixture(autouse=True)
    def _top_level(self, monkeypatch):
        # Run from inside an overcode agent, launches would become its children.
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)

    def _launcher(self, tmp_path):
        tmux = MockTmux()
        sessions = SessionManager(state_dir=tmp_path / "state", skip_git_detection=True)
        launcher = AgentLauncher(tmux_session="agents",
                                 tmux_manager=TmuxManager("agents", tmux=tmux),
                                 session_manager=sessions)
        return launcher, tmux

    def test_launch_applies_the_profile_to_the_command(self, home, tmp_path):
        make_skill(home / ".overcode" / "skills", "shirka")
        make_skill(home / ".claude" / "skills", "noisy")
        sl.save_profile("research", ["shirka"])
        launcher, tmux = self._launcher(tmp_path)

        session = launcher.launch(name="a", start_directory=str(tmp_path),
                                  skill_profile="research")
        assert session.skill_profile == "research"
        sent = " ".join(k[2] for k in tmux.sent_keys)
        assert "--plugin-dir" in sent
        assert "skillOverrides" in sent

    def test_unknown_profile_refuses_to_launch(self, home, tmp_path, capsys):
        launcher, tmux = self._launcher(tmp_path)
        assert launcher.launch(name="a", start_directory=str(tmp_path),
                               skill_profile="nope") is None
        assert "skill profile 'nope' not found" in capsys.readouterr().out

    def test_child_inherits_and_folder_pin_applies(self, home, tmp_path, monkeypatch):
        sl.save_profile("research", [])
        sl.save_profile("ios", [])
        sl.pin_folder(str(tmp_path), "ios")
        launcher, _ = self._launcher(tmp_path)

        pinned = launcher.launch(name="pinned", start_directory=str(tmp_path))
        assert pinned.skill_profile == "ios"

        parent = launcher.launch(name="parent", start_directory=str(tmp_path),
                                 skill_profile="research")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", parent.name)
        child = launcher.launch(name="child", start_directory=str(tmp_path))
        assert child.skill_profile == "research"
        none = launcher.launch(name="bare", start_directory=str(tmp_path),
                               skill_profile="none")
        assert none.skill_profile is None

    def test_profile_deleted_after_launch_does_not_block_restart(self, home, tmp_path, capsys):
        sl.save_profile("gone", [])
        launcher, tmux = self._launcher(tmp_path)
        session = launcher.launch(name="a", start_directory=str(tmp_path), skill_profile="gone")
        sl.delete_profile("gone")
        before = len(tmux.sent_keys)
        assert launcher._send_launch_for_session(session, session.tmux_window, fresh=True)
        assert len(tmux.sent_keys) > before
        assert "not found; launching without it" in capsys.readouterr().err


class TestSkillNames:
    def test_claude_hides_by_folder_opencode_by_frontmatter_name(self, home):
        make_skill(home / ".claude" / "skills", "overcode", name="overcode-cli")
        sl.save_profile("p", [])
        assert sl.prepare_profile("p", "claude-code").hidden == ["overcode"]
        assert sl.prepare_profile("p", "opencode").hidden == ["overcode-cli"]

    @pytest.mark.parametrize("listed", ["overcode", "overcode-cli"])
    def test_profile_may_name_a_skill_either_way(self, home, listed):
        make_skill(home / ".claude" / "skills", "overcode", name="overcode-cli")
        sl.save_profile("p", [listed])
        assert sl.prepare_profile("p", "claude-code").hidden == []
        assert sl.prepare_profile("p", "opencode").hidden == []


class TestCatalog:
    def test_most_used_first_and_always_on_marked(self, home):
        make_skill(home / ".overcode" / "skills", "rare")
        make_skill(home / ".overcode" / "skills", "popular")
        make_skill(home / ".claude" / "skills", "mine")
        rows = sl.catalog({"popular": 3, "research:rare": 1})
        assert [r.name for r in rows] == ["popular", "rare", "mine"]
        assert [r.uses for r in rows] == [3, 1, 0]
        assert rows[2].always_on == ["Claude", "opencode"]
        assert rows[0].always_on == []

    def test_usage_counts_agents_not_invocations(self):
        class S:
            def __init__(self, skills):
                self.loaded_skills = skills
        assert sl.skill_usage([S(["a", "a", "b"]), S(["a"]), S([])]) == {"a": 2, "b": 1}


class TestSkillsModal:
    def _modal(self, home):
        from overcode.tui_widgets.skills_modal import SkillsModal
        make_skill(home / ".overcode" / "skills", "shirka")
        make_skill(home / ".claude" / "skills", "noisy")
        modal = SkillsModal()
        modal._save_focus = lambda app_ref: None
        modal._show = lambda index=0: setattr(modal, "selected_index", index)
        modal.show(usage={"shirka": 2}, folder=str(home / "app"))
        return modal

    def test_create_toggle_switch_pin_delete(self, home):
        modal = self._modal(home)
        assert modal.profile is None
        modal._toggle()
        assert "press n" in modal._message.plain

        modal._create("research")
        modal._toggle()                         # shirka is first (most used)
        assert sl.get_profiles() == {"research": ["shirka"]}
        modal._toggle()
        assert sl.get_profiles() == {"research": []}

        modal._create("ios")
        modal._switch(1)
        assert modal.profile == "research"

        modal._pin()
        assert sl.get_folder_pins() == {"~/app": "research"}

        modal._delete()
        assert "research" in sl.get_profiles()  # first press only asks
        modal._delete()
        assert list(sl.get_profiles()) == ["ios"]
        assert sl.get_folder_pins() == {}

    def test_opens_on_the_folders_pinned_profile(self, home):
        sl.save_profile("a", [])
        sl.save_profile("b", [])
        sl.pin_folder(str(home / "app"), "b")
        assert self._modal(home).profile == "b"

    def test_render_marks_hidden_always_on_skills(self, home):
        sl.save_profile("research", ["shirka"])
        modal = self._modal(home)
        plain = modal.render().plain
        assert "research" in plain
        assert "on: Claude+opencode" in plain
        assert "hidden" in plain

    async def test_mounted_in_an_app(self, home):
        from textual.app import App
        from overcode.tui_widgets.skills_modal import SkillsModal
        make_skill(home / ".overcode" / "skills", "shirka")
        sl.save_profile("research", [])

        class Host(App):
            def compose(self):
                yield SkillsModal(id="m")

        async with Host().run_test() as pilot:
            modal = pilot.app.query_one("#m", SkillsModal)
            modal.show(usage={}, folder=str(home), app_ref=pilot.app)
            await pilot.press("space")
            assert sl.get_profiles() == {"research": ["shirka"]}
            # The name starts as the folder's name ("home"); typing appends.
            await pilot.press("n", "x", "enter")
            assert modal.profile == "homex"
            assert "homex" in sl.get_profiles()


class TestRenamedSkills:
    def test_old_name_used_only_once_it_matches_nothing(self, home):
        sl.save_profile("p", ["delegating-to-agents"])
        # Before the merge: the old skill still exists, so it's itself.
        make_skill(home / ".claude" / "skills", "delegating-to-agents")
        make_skill(home / ".claude" / "skills", "overcode", name="overcode-cli")
        assert sl.prepare_profile("p", "claude-code").hidden == ["overcode"]

    def test_after_the_merge_the_profile_keeps_the_new_skill(self, home):
        sl.save_profile("p", ["overcode-cli", "delegating-to-agents"])
        make_skill(home / ".claude" / "skills", "overcode")          # name: overcode now
        make_skill(home / ".claude" / "skills", "other")
        assert sl.prepare_profile("p", "claude-code").hidden == ["other"]
        assert sl.prepare_profile("p", "opencode").hidden == ["other"]
