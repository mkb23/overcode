"""Tests for the overagent (#484): its backend, skills, and the `e` key."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from overcode.backends import BackendCapability, get_backend, supports
from overcode.backends.base import LaunchSpec
from overcode.overagent import (
    ALLOW,
    CONFIGURATOR_SKILL,
    DEFAULT_NAME,
    SYSTEM_PROMPT,
    docs_location,
    find_overagent,
)


def _settings(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])


@pytest.fixture(autouse=True)
def _overcode_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))


class TestBackend:
    def test_is_claude_code_with_its_capabilities(self):
        b = get_backend("overagent")
        assert b.name == "overagent" and b.binary == "claude"
        for cap in (BackendCapability.RESUME, BackendCapability.HOOK_EVENTS,
                    BackendCapability.TRANSCRIPT_STATS, BackendCapability.SKILLS):
            assert supports(b, cap)

    def test_adds_persona_and_its_own_allow_list(self):
        cmd = get_backend("overagent").build_command(LaunchSpec(name="overcode", prescribed_session_id="abc"))
        prompt_file = cmd[cmd.index("--append-system-prompt-file") + 1]
        assert open(prompt_file).read() == SYSTEM_PROMPT
        assert not any("\n" in arg for arg in cmd if not arg.startswith("{"))  # nothing multi-line on the shell line
        s = _settings(cmd)
        assert s["permissions"]["allow"] == list(ALLOW)
        assert s["hooks"]  # hooks still injected: status and stats work as for Claude
        assert "--session-id" in cmd

    def test_default_allow_list_is_replaced_not_extended(self):
        allow = _settings(get_backend("overagent").build_command(LaunchSpec()))["permissions"]["allow"]
        words = {w.strip("()*:") for p in allow for w in p.split()}
        assert not words & {"kill", "budget", "launch", "send", "restart", "instruct", "cleanup"}
        assert "Bash(overcode view *)" in allow and "Bash(overcode view)" in allow

    @pytest.mark.parametrize("spec", [
        LaunchSpec(dangerously_skip_permissions=True),
        LaunchSpec(permissiveness_mode="bypass"),
        LaunchSpec(skip_permissions=True),
        LaunchSpec(permissiveness_mode="permissive"),
        LaunchSpec(include_punchy_perms=True),
    ])
    def test_never_bypasses_permissions(self, spec):
        cmd = get_backend("overagent").build_command(spec)
        assert "--dangerously-skip-permissions" not in cmd
        assert "dontAsk" not in cmd
        assert _settings(cmd)["permissions"]["allow"] == list(ALLOW)

    def test_resume_keeps_the_persona(self):
        cmd = get_backend("overagent").build_command(LaunchSpec(resume_session_id="old"))
        assert "--resume" in cmd and "--append-system-prompt-file" in cmd

    def test_claude_code_itself_is_unchanged(self):
        cmd = get_backend("claude-code").build_command(LaunchSpec())
        assert "--append-system-prompt-file" not in cmd

    def test_prepare_launch_installs_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        get_backend("overagent").prepare_launch(LaunchSpec())
        skill = tmp_path / ".claude" / "skills" / "overcode-configurator" / "SKILL.md"
        assert skill.read_text() == CONFIGURATOR_SKILL

    def test_prepare_launch_survives_an_unwritable_home(self, tmp_path, monkeypatch):
        blocker = tmp_path / "home"
        blocker.write_text("")
        monkeypatch.setattr("pathlib.Path.home", lambda: blocker)
        get_backend("overagent").prepare_launch(LaunchSpec())  # no raise


class TestSkills:
    def test_configurator_is_bundled(self):
        from overcode.bundled_skills import OVERCODE_SKILLS
        assert OVERCODE_SKILLS["overcode-configurator"]["content"] == CONFIGURATOR_SKILL
        assert CONFIGURATOR_SKILL.startswith("---\nname: overcode-configurator\n")

    def test_install_counts(self, tmp_path):
        from overcode.bundled_skills import OVERCODE_SKILLS, install_bundled_skills
        n = len(OVERCODE_SKILLS)
        assert install_bundled_skills(tmp_path) == (n, 0, 0)
        assert install_bundled_skills(tmp_path) == (0, 0, n)
        (tmp_path / "overcode" / "SKILL.md").write_text("old")
        assert install_bundled_skills(tmp_path) == (0, 1, n - 1)

    def test_skill_commands_exist(self):
        """Every `overcode <cmd>` the skill and prompt teach is a real command."""
        import re
        from overcode.cli import app
        top = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
        top |= {g.name for g in app.registered_groups}
        text = CONFIGURATOR_SKILL + SYSTEM_PROMPT
        used = set(re.findall(r"(?:^|`)overcode ([a-z-]+)", text, re.MULTILINE))
        assert {"view", "activity", "docs", "list"} <= used
        assert used - top == set()


class TestFindOveragent:
    def test_latest_live_one(self):
        s = [SimpleNamespace(backend="overagent", status="running", start_time="2026-09-01", id="a"),
             SimpleNamespace(backend="overagent", status="running", start_time="2026-09-02", id="b"),
             SimpleNamespace(backend="overagent", status="terminated", start_time="2026-09-03", id="c"),
             SimpleNamespace(backend="claude-code", status="running", start_time="2026-09-04", id="d")]
        assert find_overagent(s).id == "b"

    def test_none(self):
        assert find_overagent([SimpleNamespace(backend="claude-code", status="running")]) is None


def test_docs_location_finds_the_checkout():
    loc = docs_location()
    assert loc.endswith("docs") or loc.startswith("https://")


class TestOpenOveragentKey:
    def _tui(self, sessions, widgets=()):
        from overcode.tui_actions.overagent import OveragentMixin
        tui = MagicMock()
        tui.sessions = sessions
        tui._overagent_pending = False
        tui._get_widgets_in_session_order.return_value = list(widgets)
        tui.compact = False
        for name in ("action_open_overagent", "_focus_overagent", "_launch_overagent"):
            setattr(tui, name, getattr(OveragentMixin, name).__get__(tui))
        return tui

    def test_focuses_the_existing_overagent(self):
        oa = SimpleNamespace(backend="overagent", status="running", start_time="1", id="oa", name="overcode")
        other = SimpleNamespace(id="x", name="x")
        tui = self._tui([oa], [SimpleNamespace(session=other), SimpleNamespace(session=oa)])
        tui.action_open_overagent()
        assert tui.focused_session_index == 1
        tui._launch_overagent_async.assert_not_called()

    def test_launches_one_with_a_free_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
        taken = SimpleNamespace(backend="claude-code", status="running", name=DEFAULT_NAME, id="t")
        tui = self._tui([taken])
        tui.action_open_overagent()
        name, directory, backend = tui._launch_overagent_async.call_args[0]
        assert name == f"{DEFAULT_NAME}-2" and backend == "overagent" and directory == str(tmp_path)
        assert tui._overagent_pending is True

    def test_second_press_while_starting_does_not_launch_again(self):
        tui = self._tui([])
        tui._overagent_pending = True
        tui.action_open_overagent()
        tui._launch_overagent_async.assert_not_called()


class TestNameColumn:
    def _ctx(self, backend, monochrome=False):
        from types import SimpleNamespace
        return SimpleNamespace(
            session=SimpleNamespace(backend=backend), display_name="overagent  ", bg="",
            mono=lambda colored, simple="bold": simple if monochrome else colored)

    def test_overagent_name_has_its_own_colour(self):
        from overcode.summary_columns import render_agent_name
        (text, style), = render_agent_name(self._ctx("overagent"))
        assert text == "overagent  " and "#ff87ff" in style
        (_, normal), = render_agent_name(self._ctx("claude-code"))
        assert "cyan" in normal

    def test_monochrome_underlines_it(self):
        from overcode.summary_columns import render_agent_name
        (_, style), = render_agent_name(self._ctx("overagent", monochrome=True))
        assert "underline" in style

    def test_default_name(self):
        assert DEFAULT_NAME == "overagent"
