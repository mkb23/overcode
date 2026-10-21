"""The naming policy on the CLI (#542, auto-naming.md §2a): launch/fork from an
agent, `name check`, `name policy`, `rename --auto/--lock`, `list --json`."""

import json

import pytest
from typer.testing import CliRunner

from overcode import config
from overcode.cli import app
from tests.unit.test_cli import fleet, strip_ansi  # noqa: F401  (fixture)

pytestmark = pytest.mark.unit

runner = CliRunner()


@pytest.fixture
def naming_config(tmp_path, monkeypatch):
    """Write config.yaml's text; a fresh read each time."""
    def write(text=""):
        path = tmp_path / "config.yaml"
        path.write_text(text)
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        config._clear_config_cache()
    write()
    yield write
    config._clear_config_cache()


FORBID = "naming:\n  advice: name the work\n  rules:\n    forbid: [child, tmp]\n"


@pytest.fixture
def in_agent(fleet, monkeypatch):  # noqa: F811
    """Run the CLI as the agent 'parent' would."""
    parent = fleet.add("parent")
    monkeypatch.setenv("OVERCODE_SESSION_ID", parent.id)
    monkeypatch.setenv("OVERCODE_SESSION_NAME", "parent")
    return parent


def _launch(*args):
    return runner.invoke(app, ["launch", "--directory", "/tmp", *args])


class TestLaunchFromAnAgent:

    def test_a_passing_name_is_a_parent_name(self, fleet, in_agent, naming_config):  # noqa: F811
        naming_config(FORBID)
        result = _launch("-n", "jwt-refresh")
        assert result.exit_code == 0, result.output
        child = fleet.get("jwt-refresh")
        assert (child.name_source, child.name_forced) == ("parent", False)

    def test_a_policy_failure_exits_3_and_launches_nothing(self, fleet, in_agent, naming_config):  # noqa: F811
        naming_config(FORBID)
        result = _launch("-n", "child-1")
        assert result.exit_code == 3
        err = " ".join(strip_ansi(result.stderr).split())
        assert "forbidden word 'child'" in err
        assert "name the work" in err and "--force-name" in err
        assert fleet.get("child-1") is None

    def test_json_refusal(self, fleet, in_agent, naming_config):  # noqa: F811
        naming_config(FORBID)
        result = _launch("-n", "tmp", "--json")
        assert result.exit_code == 3
        data = json.loads(result.stdout)
        assert data["error"] == "naming_policy" and data["ok"] is False
        assert data["failures"][0]["rule"] == "forbid"
        assert data["advice"] == "name the work"

    def test_force_name_launches_and_records_it(self, fleet, in_agent, naming_config):  # noqa: F811
        naming_config(FORBID)
        result = _launch("-n", "child-1", "--force-name")
        assert result.exit_code == 0, result.output
        child = fleet.get("child-1")
        assert (child.name_source, child.name_forced) == ("parent", True)

    def test_an_existing_agents_name_is_the_launchers_to_settle(self, fleet, in_agent, naming_config):  # noqa: F811
        """A parent relaunching a child gets it back (live) or a fresh one
        (stale window), as before the policy: never exit 3."""
        fleet.add("busy")
        result = _launch("-n", "busy")
        assert result.exit_code != 3, result.output
        assert "taken" not in " ".join(strip_ansi(result.stderr).split())

    def test_force_name_never_gets_past_an_alias(self, fleet, in_agent, naming_config):  # noqa: F811
        fleet.add("new", previous_names=["old"])
        result = _launch("-n", "old", "--force-name")
        assert result.exit_code == 3
        assert "old name of 'new'" in " ".join(strip_ansi(result.stderr).split())

    def test_an_alias_is_taken(self, fleet, in_agent, naming_config):  # noqa: F811
        fleet.add("new", previous_names=["old"])
        result = _launch("-n", "old")
        assert result.exit_code == 3
        assert "old name of 'new'" in " ".join(strip_ansi(result.stderr).split())

    def test_defaults_let_a_numbered_series_through(self, fleet, in_agent, naming_config):  # noqa: F811
        fleet.add("wiki1")
        fleet.add("wiki2")
        result = _launch("-n", "wiki3")
        assert result.exit_code == 0, result.output

    def test_json_success_is_the_only_stdout(self, fleet, in_agent, naming_config):  # noqa: F811
        result = _launch("-n", "jwt", "--json")
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["name"] == "jwt" and data["name_source"] == "parent"
        assert data["parent_session_id"] == in_agent.id
        assert "launched" in result.stderr

    def test_llm_check_refuses_and_fails_open(self, fleet, in_agent, naming_config, monkeypatch):  # noqa: F811
        from overcode.naming_policy import RULE_ADVICE, Failure
        naming_config("naming:\n  advice: name the work\n  rules:\n    llm_check: true\n")
        calls = []

        def fake(name, advice, **kw):
            calls.append((name, advice))
            return (Failure(RULE_ADVICE, "advice: names the tool"), "auth-fix") if name == "claude" else None
        monkeypatch.setattr("overcode.naming_policy.llm_check", fake)
        result = _launch("-n", "claude")
        assert result.exit_code == 3
        assert "try 'auth-fix'" in " ".join(strip_ansi(result.stderr).split())
        assert _launch("-n", "auth-fix").exit_code == 0
        assert calls == [("claude", "name the work"), ("auth-fix", "name the work")]


class TestLaunchFromAHuman:

    def test_never_refused(self, fleet, naming_config):  # noqa: F811
        naming_config(FORBID)
        result = _launch("-n", "child-1")
        assert result.exit_code == 0, result.output
        child = fleet.get("child-1")
        assert (child.name_source, child.name_forced) == ("human", False)
        assert "naming policy" not in result.stderr

    def test_warn_humans(self, fleet, naming_config):  # noqa: F811
        naming_config(FORBID + "    warn_humans: true\n")
        result = _launch("-n", "child-1")
        assert result.exit_code == 0, result.output
        assert "Warning" in result.stderr and "forbidden word" in result.stderr


class TestFork:

    def _source(self, fleet):  # noqa: F811
        return fleet.add("src", active_agent_session_id="conv-1", start_directory="/tmp")

    def test_default_fork_name_is_default(self, fleet, naming_config, monkeypatch):  # noqa: F811
        self._source(fleet)
        result = runner.invoke(app, ["fork", "src"])
        assert result.exit_code == 0, result.output
        assert fleet.get("src-fork").name_source == "default"

    def test_an_agent_forking_with_a_bad_name(self, fleet, in_agent, naming_config):  # noqa: F811
        naming_config(FORBID)
        self._source(fleet)
        result = runner.invoke(app, ["fork", "src", "-n", "tmp"])
        assert result.exit_code == 3
        result = runner.invoke(app, ["fork", "src", "-n", "side-study"])
        assert result.exit_code == 0, result.output
        assert fleet.get("side-study").name_source == "parent"


class TestNameCommands:

    def test_check_passes(self, fleet, naming_config):  # noqa: F811
        result = runner.invoke(app, ["name", "check", "jwt-refresh"])
        assert result.exit_code == 0
        assert "passes" in result.output

    def test_check_fails_with_3(self, fleet, naming_config):  # noqa: F811
        naming_config(FORBID)
        fleet.add("alpha")
        result = runner.invoke(app, ["name", "check", "alpha", "--json"])
        assert result.exit_code == 3
        data = json.loads(result.stdout)
        assert data["hard"] is True and data["suggestion"] == "alpha-2"

    def test_policy_puts_the_advice_first(self, naming_config):
        naming_config(FORBID)
        out = strip_ansi(runner.invoke(app, ["name", "policy"]).output)
        assert out.index("Naming advice") < out.index("name the work") < out.index("Rules")
        assert "forbid: no word from child, tmp" in out
        assert "exits 3" in out

    def test_policy_with_nothing_configured(self, naming_config):
        out = strip_ansi(runner.invoke(app, ["name", "policy"]).output)
        assert "overcode's default" in out
        assert "none set" in out

    def test_policy_json(self, naming_config):
        naming_config(FORBID)
        data = json.loads(runner.invoke(app, ["name", "policy", "--json"]).stdout)
        assert data["advice"] == "name the work" and data["exit_code_on_refusal"] == 3
        assert data["rules"]["forbid"] == ["child", "tmp"]


class TestAutoNamingSwitch:

    def test_policy_says_whether_auto_naming_is_on(self, naming_config):
        out = strip_ansi(runner.invoke(app, ["name", "policy"]).output)
        assert "Auto-naming off" in out
        naming_config("naming:\n  enabled: true\n")
        out = strip_ansi(runner.invoke(app, ["name", "policy"]).output)
        assert "Auto-naming on: a default name is replaced 5 min after its first prompt" in out
        data = json.loads(runner.invoke(app, ["name", "policy", "--json"]).stdout)
        assert data["enabled"] is True and data["first_after"] == {"minutes": 5.0}

    def test_describe(self, naming_config):
        assert config.describe_auto_naming().startswith("off")
        naming_config("naming:\n  enabled: true\n  first_after: {minutes: 0}\n")
        assert "as soon as it starts on its first prompt" in config.describe_auto_naming()

    def test_the_switch_keeps_the_rest_of_naming(self, naming_config):
        naming_config(FORBID)
        config.save_naming_enabled(True)
        config._clear_config_cache()
        cfg = config.get_naming_config()
        assert cfg["enabled"] is True and cfg["advice"] == "name the work"
        assert cfg["rules"]["forbid"] == ["child", "tmp"]
        config.save_naming_enabled(False)
        config._clear_config_cache()
        assert config.get_naming_config()["enabled"] is False

    def test_the_new_agent_dialog_says_what_happens_to_the_name(self, naming_config):
        from overcode.tui_widgets.new_agent_modal import FormField, NewAgentModal
        field = FormField("name", "Name", "text", value="overcode-main2", auto=True)
        assert "auto-naming off" in NewAgentModal._name_tip(field)
        naming_config("naming:\n  enabled: true\n")
        assert "auto-naming renames it 5 min after its first prompt" in NewAgentModal._name_tip(field)
        field.auto = False
        assert "a name you type is kept" in NewAgentModal._name_tip(field)


class TestRename:

    def test_auto_and_lock_keep_the_name(self, fleet, naming_config):  # noqa: F811
        fleet.add("alpha")
        result = runner.invoke(app, ["rename", "alpha", "--auto"])
        assert result.exit_code == 0, result.output
        # Handed to overcode: a placeholder it may replace, not a name it chose (no "~")
        assert (fleet.get("alpha").name, fleet.get("alpha").name_source) == ("alpha", "default")
        result = runner.invoke(app, ["rename", "alpha", "--lock"])
        assert result.exit_code == 0, result.output
        assert fleet.get("alpha").name_source == "human"

    @pytest.mark.parametrize("args", [
        ["alpha"], ["alpha", "beta", "--auto"], ["alpha", "--auto", "--lock"],
    ])
    def test_bad_combinations(self, fleet, args):  # noqa: F811
        fleet.add("alpha")
        assert runner.invoke(app, ["rename", *args]).exit_code == 1

    def test_a_rename_locks_the_name(self, fleet):  # noqa: F811
        fleet.add("alpha", window=False, name_source="auto")
        result = runner.invoke(app, ["rename", "alpha", "beta"])
        assert result.exit_code == 0, result.output
        assert fleet.get("beta").name_source == "human"


class TestListJson:

    def test_source_and_previous_names(self, fleet):  # noqa: F811
        fleet.add("beta", previous_names=["alpha"], name_source="auto")
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0, result.output
        [row] = json.loads(result.stdout)
        assert (row["name"], row["name_source"], row["previous_names"]) == ("beta", "auto", ["alpha"])
        assert row["name_forced"] is False

    def test_the_text_list_marks_auto_names(self, fleet):  # noqa: F811
        fleet.add("beta", name_source="auto")
        fleet.add("gamma")
        out = strip_ansi(runner.invoke(app, ["list"]).output)
        assert "~beta" in out and "~gamma" not in out


async def test_the_palette_switch_turns_auto_naming_on_and_off(make_tui, naming_config):
    from overcode.command_palette import COMMANDS
    entry = next(c for c in COMMANDS if c.action == "toggle_auto_naming")
    app = make_tui()
    async with app.run_test(size=(160, 40)) as pilot:
        assert entry.state(app).current == 0             # off
        app.action_toggle_auto_naming()
        await pilot.pause()
        config._clear_config_cache()
        assert config.get_naming_config()["enabled"] is True
        assert entry.state(app).current == 1             # on
        app.action_toggle_auto_naming()
        config._clear_config_cache()
        assert config.get_naming_config()["enabled"] is False
