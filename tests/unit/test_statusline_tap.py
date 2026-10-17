"""The opt-in calibration statusline: sampling, chaining the user's own, launch wiring."""

import io
import json
import os
import sys
from unittest.mock import patch

import pytest

from overcode import paths, statusline_tap
from overcode.backends import LaunchSpec
from overcode.backends.claude_code import ClaudeCodeBackend

PAYLOAD = {
    "session_id": "claude-sess-1",
    "model": {"id": "claude-opus-x", "display_name": "Opus"},
    "workspace": {"current_dir": "", "project_dir": ""},
    "cost": {"total_cost_usd": 1.25},
    "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 1800001000},
                    "seven_day": {"used_percentage": 37, "resets_at": 1800200000}},
    "prompt_cache": {"hits": 10, "misses": 2, "miss_causes": {"ttl": 2}},
}


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    usage = tmp_path / "usage"
    monkeypatch.setattr(paths, "usage_dir", lambda registry_dir=None: usage)
    return home


def _run(payload, env=None):
    out = io.StringIO()
    code = statusline_tap.main(stdin=io.StringIO(json.dumps(payload)), stdout=out,
                               env=env or {"OVERCODE_SESSION_ID": "agent-1"})
    return code, out.getvalue()


def _samples():
    path = paths.usage_dir() / "statusline" / "agent-1.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class TestSampling:
    def test_records_rate_limits_cost_and_cache_stats(self, claude_home):
        code, out = _run(PAYLOAD)
        assert code == 0 and out == ""
        (sample,) = _samples()
        assert sample["sid"] == "agent-1" and sample["csid"] == "claude-sess-1"
        assert sample["rl"]["seven_day"]["used_percentage"] == 37
        assert sample["cost"] == 1.25
        assert sample["pc"]["prompt_cache"]["miss_causes"] == {"ttl": 2}
        assert sample["model"] == "claude-opus-x"

    def test_unchanged_sample_not_written_again(self, claude_home):
        _run(PAYLOAD)
        _run(PAYLOAD)
        changed = dict(PAYLOAD, cost={"total_cost_usd": 2.0})
        _run(changed)
        assert [s["cost"] for s in _samples()] == [1.25, 2.0]

    def test_garbage_input_never_fails(self, claude_home):
        out = io.StringIO()
        assert statusline_tap.main(stdin=io.StringIO("not json"), stdout=out, env={}) == 0
        assert out.getvalue() == ""


class TestChaining:
    def _user_statusline(self, home, command, padding=None):
        line = {"type": "command", "command": command}
        if padding is not None:
            line["padding"] = padding
        (home / "settings.json").write_text(json.dumps({"statusLine": line}))

    def test_user_statusline_output_printed(self, claude_home):
        script = claude_home / "mine.py"
        script.write_text("import json,sys; d=json.load(sys.stdin); print('MINE ' + d['model']['display_name'])")
        self._user_statusline(claude_home, f"{sys.executable} {script}")
        code, out = _run(PAYLOAD)
        assert code == 0
        assert out == "MINE Opus\n"
        assert len(_samples()) == 1  # sampled as well

    def test_project_statusline_wins_over_user(self, claude_home, tmp_path):
        self._user_statusline(claude_home, "echo user")
        project = tmp_path / "proj"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "echo project"}}))
        payload = dict(PAYLOAD, workspace={"project_dir": str(project)})
        assert _run(payload)[1] == "project\n"

    def test_failing_user_statusline_does_not_break(self, claude_home):
        self._user_statusline(claude_home, "exit 3")
        assert _run(PAYLOAD) == (0, "")

    def test_itself_is_never_chained(self, claude_home):
        self._user_statusline(claude_home, "overcode statusline")
        assert statusline_tap.find_user_statusline(None) is None


class TestLaunchWiring:
    def _settings(self, calibration, claude_home=None):
        with patch("overcode.backends.claude_code._resolve_overcode_bin", return_value="/bin/oc"), \
                patch("overcode.config.get_quota_config", return_value={"calibration": calibration}):
            cmd = ClaudeCodeBackend().build_command(LaunchSpec())
        return json.loads(cmd[cmd.index("--settings") + 1])

    def test_disabled_adds_nothing(self, claude_home):
        assert "statusLine" not in self._settings(False)

    def test_enabled_adds_statusline_keeping_user_padding(self, claude_home):
        (claude_home / "settings.json").write_text(
            json.dumps({"statusLine": {"type": "command", "command": "echo hi", "padding": 2}}))
        assert self._settings(True)["statusLine"] == {
            "type": "command", "command": "/bin/oc statusline", "padding": 2}

    def test_entrypoint_fast_path(self, claude_home, monkeypatch):
        from overcode import entrypoint
        monkeypatch.setattr(sys, "argv", ["overcode", "statusline"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(PAYLOAD)))
        monkeypatch.setenv("OVERCODE_SESSION_ID", "agent-1")
        with patch("overcode.cli.main", side_effect=AssertionError("CLI imported")):
            entrypoint.main()
        assert len(_samples()) == 1


def test_default_config_is_off():
    assert statusline_tap.launch_setting("/bin/oc", {"calibration": False}) is None
    assert os.environ.get("OVERCODE_NO_USAGE_FETCH") == "1"
