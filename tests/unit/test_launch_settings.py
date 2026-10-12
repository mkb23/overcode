"""Table tests for ``resolve_launch_settings`` (#433, #499)."""

from types import SimpleNamespace

import pytest

from overcode.backends import DEFAULT_BACKEND
from overcode.launch_settings import LaunchArgs, LaunchPlan, resolve_launch_settings


def parent(**overrides):
    fields = dict(
        backend="claude-code", provider="bedrock", model="opus", wrapper="devcontainer",
        agent_teams=True, permissiveness_mode="bypass", skill_profile="web",
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def plan(**overrides):
    fields = dict(
        backend=DEFAULT_BACKEND, provider="web", model=None, wrapper=None,
        agent_teams=False, permissiveness_mode="normal", skill_profile=None,
    )
    fields.update(overrides)
    return LaunchPlan(**fields)


CASES = [
    # id, args, parent, defaults, folder pin, expected
    ("top-level, nothing set", LaunchArgs(), None, {}, None, plan()),
    ("config defaults fill the gaps",
     LaunchArgs(), None,
     {"provider": "bedrock", "wrapper": "w", "backend": "codex", "skill_profile": "p"}, None,
     plan(provider="bedrock", wrapper="w", backend="codex", skill_profile="p")),
    ("empty config values are unset",
     LaunchArgs(), None, {"provider": "", "wrapper": "", "backend": ""}, None, plan()),
    ("explicit beats config",
     LaunchArgs(provider="web", wrapper="x", backend="grok", model="m"), None,
     {"provider": "bedrock", "wrapper": "w", "backend": "codex"}, None,
     plan(provider="web", wrapper="x", backend="grok", model="m")),
    ("permission flags", LaunchArgs(skip_permissions=True), None, {}, None,
     plan(permissiveness_mode="permissive")),
    ("bypass wins over permissive",
     LaunchArgs(skip_permissions=True, dangerously_skip_permissions=True), None, {}, None,
     plan(permissiveness_mode="bypass")),
    ("child inherits everything", LaunchArgs(), parent(), {"provider": "web"}, None,
     plan(backend="claude-code", provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="bypass", skill_profile="web")),
    ("child inherits permissive", LaunchArgs(), parent(permissiveness_mode="permissive"), {}, None,
     plan(backend="claude-code", provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="permissive", skill_profile="web")),
    ("an explicit permission flag is not widened by the parent",
     LaunchArgs(skip_permissions=True), parent(), {}, None,
     plan(backend="claude-code", provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="permissive", skill_profile="web")),
    ("explicit beats parent",
     LaunchArgs(provider="web", model="haiku", wrapper="other", skill_profile="none"),
     parent(), {}, None,
     plan(backend="claude-code", provider="web", model="haiku", wrapper="other",
          agent_teams=True, permissiveness_mode="bypass", skill_profile=None)),
    ("cross-backend child keeps neither model nor provider",
     LaunchArgs(backend="codex"), parent(), {"provider": "web"}, None,
     plan(backend="codex", provider="web", model=None, wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="bypass", skill_profile="web")),
    ("same backend named explicitly still inherits model",
     LaunchArgs(backend="claude-code"), parent(), {}, None,
     plan(backend="claude-code", provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="bypass", skill_profile="web")),
    ("a parent with no backend is the default backend",
     LaunchArgs(backend=DEFAULT_BACKEND), parent(backend=None), {}, None,
     plan(provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="bypass", skill_profile="web")),
    ("overagent children are ordinary agents",
     LaunchArgs(), parent(backend="overagent"), {"backend": "codex"}, None,
     plan(backend="codex", provider="bedrock", model="opus", wrapper="devcontainer",
          agent_teams=True, permissiveness_mode="bypass", skill_profile="web")),
    ("no inheritance when opted out",
     LaunchArgs(inherit_parent_settings=False), parent(), {}, None, plan()),
    ("folder pin applies when caller and parent name no profile",
     LaunchArgs(), None, {"skill_profile": "default-p"}, "pinned", plan(skill_profile="pinned")),
    ("config profile when nothing else",
     LaunchArgs(), None, {"skill_profile": "default-p"}, None, plan(skill_profile="default-p")),
    ("'none' pinned to the folder stops the search",
     LaunchArgs(), None, {"skill_profile": "default-p"}, "none", plan()),
    ("the parent's profile beats the folder pin",
     LaunchArgs(), parent(backend=None, provider="web", model=None, wrapper=None,
                          agent_teams=False, permissiveness_mode="normal"), {}, "pinned",
     plan(skill_profile="web")),
]


@pytest.mark.parametrize(
    "args, parent_session, defaults, folder, expected",
    [c[1:] for c in CASES], ids=[c[0] for c in CASES],
)
def test_resolve_launch_settings(args, parent_session, defaults, folder, expected):
    assert resolve_launch_settings(args, parent_session, defaults,
                                   folder_profile=lambda: folder) == expected


def test_the_folder_pin_is_read_only_when_needed():
    def boom():
        raise AssertionError("folder pins read")

    resolved = resolve_launch_settings(LaunchArgs(skill_profile="x"), None, {}, folder_profile=boom)
    assert resolved.skill_profile == "x"
