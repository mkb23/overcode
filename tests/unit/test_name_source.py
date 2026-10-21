"""Where a name came from (#542): the field, old records, and the naming config."""

import pytest

from overcode import config
from overcode.name_source import (
    AUTO, DEFAULT, HUMAN, PARENT, cli_source, infer_legacy, is_fork_default, is_locked,
    name_marker,
)
from overcode.session_manager import Session

pytestmark = pytest.mark.unit


def _record(name, start_directory="/work/overcode-main", **extra):
    return {"id": "id-1", "name": name, "tmux_session": "agents", "tmux_window": f"{name}-id-1",
            "command": ["claude"], "start_directory": start_directory,
            "start_time": "2026-09-01T10:00:00", **extra}


class TestLegacyRecords:

    @pytest.mark.parametrize("name", [
        "overcode-main", "overcode-main2", "overcode-main17", "overcode-main_new",
        "auth-fork", "auth-fork2", "auth-fork_100",
    ])
    def test_names_overcode_made_up_are_default(self, name):
        assert infer_legacy(_record(name)) == DEFAULT

    @pytest.mark.parametrize("name, start", [
        ("jwt-refresh", "/work/overcode-main"),
        ("overcode-main-fix", "/work/overcode-main"),
        ("overcode-main1", "/work/overcode-main"),   # the modal starts at 2
        ("overcode-main100", "/work/overcode-main"),
        ("forked", "/work/x"),
        ("main", None),
        ("fork", "/work/x"),
    ])
    def test_anything_else_is_human(self, name, start):
        assert infer_legacy(_record(name, start)) == HUMAN

    def test_an_old_sessions_json_record_loads_with_a_source(self):
        assert Session.from_dict(_record("overcode-main")).name_source == DEFAULT
        s = Session.from_dict(_record("my-agent"))
        assert (s.name_source, s.name_forced) == (HUMAN, False)

    def test_a_written_source_wins(self):
        s = Session.from_dict(_record("overcode-main", name_source=AUTO, name_forced=True))
        assert (s.name_source, s.name_forced) == (AUTO, True)

    def test_round_trip(self):
        s = Session.from_dict(_record("x", name_source=PARENT))
        assert Session.from_dict(s.to_dict()).name_source == PARENT

    def test_from_dict_does_not_mutate(self):
        record = _record("overcode-main")
        Session.from_dict(record)
        assert "name_source" not in record


class TestHelpers:

    def test_locks(self):
        assert [is_locked(s) for s in (DEFAULT, AUTO, PARENT, HUMAN, None)] == [
            False, False, True, True, True]

    def test_marker(self):
        assert [name_marker(s) for s in (DEFAULT, AUTO, PARENT, HUMAN)] == ["", "~", "", ""]

    def test_cli_source(self):
        assert cli_source({}) == HUMAN
        assert cli_source({"OVERCODE_SESSION_ID": "abc"}) == PARENT

    def test_fork_default(self):
        assert is_fork_default("alpha-fork", "alpha")
        assert is_fork_default("alpha-fork3", "alpha")
        assert not is_fork_default("alpha-fork", "beta")
        assert not is_fork_default("alpha-forked", "alpha")


class TestSessionManager:

    def test_create_session_records_the_source(self, tmp_path, monkeypatch):
        from overcode.session_manager import SessionManager
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        sm = SessionManager(skip_git_detection=True)
        s = sm.create_session(name="child", tmux_session="agents", tmux_window="w",
                              command=[], name_source=PARENT, name_forced=True)
        back = sm.get_session(s.id)
        assert (back.name_source, back.name_forced) == (PARENT, True)
        plain = sm.create_session(name="mine", tmux_session="agents", tmux_window="w2", command=[])
        assert sm.get_session(plain.id).name_source == HUMAN

    def test_the_archive_keeps_the_source(self):
        from overcode.session_archive import slim_record
        slim = slim_record(_record("x", name_source=AUTO, name_forced=True))
        assert (slim["name_source"], slim["name_forced"]) == (AUTO, True)


@pytest.fixture
def load_naming(tmp_path, monkeypatch):
    def load(text):
        path = tmp_path / "config.yaml"
        path.write_text(text)
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        config._clear_config_cache()
        return config.get_naming_config()
    yield load
    config._clear_config_cache()


class TestNamingConfig:

    def test_defaults_reject_nothing(self, load_naming):
        cfg = load_naming("tmux_session: agents\n")
        assert cfg["enabled"] is False
        assert cfg["advice"] == ""
        assert cfg["max_length"] == 18
        assert cfg["rules"] == {
            "pattern": None, "max_length": None, "forbid": [], "require": None,
            "no_repo_prefix": False, "recent_archived_hours": 0.0, "llm_check": False,
            "warn_humans": False,
        }
        assert cfg["first_after"] == {"minutes": 5.0}
        assert (cfg["max_renames"], cfg["cost_cap_per_day"], cfg["warnings"]) == (2, 0.5, [])

    def test_a_full_block(self, load_naming):
        cfg = load_naming(
            "naming:\n"
            "  enabled: true\n"
            "  advice: |\n    kebab-case, 1-3 words\n"
            "  max_length: 24\n"
            "  rules:\n"
            "    pattern: '^[a-z0-9-]+$'\n"
            "    max_length: 30\n"
            "    forbid: [Test, agent, 7]\n"
            "    require: '-[0-9]+$'\n"
            "    no_repo_prefix: true\n"
            "    recent_archived_hours: 24\n"
            "    llm_check: true\n"
            "    warn_humans: true\n"
            "  first_after: {minutes: 3}\n"
            "  max_renames: 1\n"
            "  cost_cap_per_day: 0.25\n"
        )
        assert cfg["enabled"] and cfg["advice"] == "kebab-case, 1-3 words"
        assert cfg["max_length"] == 24
        r = cfg["rules"]
        assert r["forbid"] == ["test", "agent", "7"]
        assert (r["pattern"], r["max_length"], r["require"]) == ("^[a-z0-9-]+$", 30, "-[0-9]+$")
        assert r["no_repo_prefix"] and r["llm_check"] and r["warn_humans"]
        assert r["recent_archived_hours"] == 24.0
        assert cfg["first_after"] == {"minutes": 3.0} and cfg["max_renames"] == 1
        assert cfg["warnings"] == []

    def test_bad_values_warn_and_never_raise(self, load_naming):
        cfg = load_naming(
            "naming:\n"
            "  colour: blue\n"
            "  max_length: -3\n"
            "  rules:\n"
            "    pattern: '[unclosed'\n"
            "    forbid: {a: 1}\n"
            "    speling: x\n"
        )
        text = " ".join(cfg["warnings"])
        assert "unknown key 'colour'" in text and "unknown key 'speling'" in text
        assert "invalid regex" in text and "forbid" in text
        assert cfg["max_length"] == 18 and cfg["rules"]["pattern"] is None

    def test_not_a_mapping(self, load_naming):
        cfg = load_naming("naming: yes\n")
        assert cfg["warnings"] and cfg["rules"]["forbid"] == []


class TestNamingPrompt:

    def test_default_and_saved(self, tmp_path, monkeypatch):
        from overcode import summarizer_prompts as sp
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
        assert sp.load_naming_prompt() == sp.DEFAULT_NAMING_PROMPT
        assert not sp.naming_prompt_customised()
        sp.naming_prompt_path().parent.mkdir(parents=True)
        sp.naming_prompt_path().write_text("Advice: {advice}; max {max_length}; {nope}")
        assert sp.naming_prompt_customised()
        out = sp.render_naming_prompt(sp.load_naming_prompt(), advice="short", max_length=18)
        assert out == "Advice: short; max 18; {nope}"

    def test_the_default_renders_every_placeholder(self):
        from overcode import summarizer_prompts as sp
        out = sp.render_naming_prompt(sp.DEFAULT_NAMING_PROMPT, advice="A", repo="r")
        assert "{advice}" not in out and "(none)" in out
        assert '{"candidates"' in out  # literal JSON braces survive
