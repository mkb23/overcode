"""Tests for the usage log (#483): the recorder, the CLI record, the loader, and TUI capture."""

import json
import time

import pytest

from overcode.activity_log import (
    REDACTED_CHAR,
    ActivityRecorder,
    activity_path_for,
    cli_command_path,
    iter_records,
    record_cli_invocation,
    recording_enabled,
)


def _read(directory):
    return list(iter_records(directory=directory))


class TestRecorder:
    def test_flush_appends_jsonl_to_the_month_file(self, tmp_path):
        rec = ActivityRecorder("agents", directory=tmp_path, enabled=True)
        rec.record("action", action="toggle_help", via="key", ok=True)
        rec.record("action", action="toggle_help", via="key", ok=True)
        assert rec.flush() == 2
        path = activity_path_for(time.time() * 1000, tmp_path)
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        r = json.loads(lines[0])
        assert r["kind"] == "action" and r["action"] == "toggle_help"
        assert r["v"] == 1 and r["ts"] == "agents" and r["host"] and r["sid"] == rec.sid

    def test_flush_with_nothing_buffered_writes_nothing(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        assert rec.flush() == 0
        assert not any(tmp_path.iterdir())

    def test_disabled_creates_no_record(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=False)
        rec.record("action", action="x")
        rec.record_key("j", "j", "list", True)
        assert rec.flush() == 0

    def test_paused_creates_no_record(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        rec.paused = True
        rec.record_key("j", "j", "list", True)
        assert rec.flush() == 0

    def test_none_fields_are_dropped(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        rec.record("action", action="x", ns=None)
        rec.flush()
        assert "ns" not in _read(tmp_path)[0]

    def test_key_repeat_count_and_gap(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        for _ in range(3):
            rec.record_key("j", "j", "list", True)
        rec.record_key("k", "k", "list", True)
        rec.flush()
        keys = _read(tmp_path)
        assert [r.get("rep") for r in keys] == [None, 2, 3, None]
        assert "dt" not in keys[0] and keys[1]["dt"] >= 0

    def test_char_kept_only_when_it_differs_from_the_key_name(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        rec.record_key("j", "j", "list", True)
        rec.record_key("question_mark", "?", "list", True)
        rec.record_key("escape", None, "list", False)
        rec.flush()
        a, b, c = _read(tmp_path)
        assert "char" not in a and b["char"] == "?" and "char" not in c

    @pytest.mark.parametrize("ctx", [
        "command_bar:send", "command_bar:standing_orders", "command_bar:heartbeat_instruction",
    ])
    def test_text_typed_to_agents_is_never_recorded(self, tmp_path, ctx):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        for ch in "secret":
            rec.record_key(ch, ch, ctx, True)
        rec.record_key("backspace", None, ctx, False)
        rec.record_key("enter", None, ctx, False)
        rec.flush()
        records = _read(tmp_path)
        assert "secret" not in json.dumps(records)
        assert [r["key"] for r in records] == [REDACTED_CHAR] * 6 + ["backspace", "enter"]

    @pytest.mark.parametrize("ctx", ["command_bar:annotation", "command_bar:fork_name", "modal:rename-agent-modal"])
    def test_overcode_metadata_text_is_recorded(self, tmp_path, ctx):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        rec.record_key("x", "x", ctx, True)
        rec.flush()
        assert _read(tmp_path)[0]["key"] == "x"

    def test_records_land_in_their_own_month(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        rec.record("a")
        rec.record("b")
        rec._buffer[0]["t"] = 1767225600000.0 - 1  # just before 2026-01-01 local-ish
        rec.flush()
        assert len(list(tmp_path.glob("*.jsonl"))) in (1, 2)
        assert len(_read(tmp_path)) == 2

    def test_unwritable_directory_drops_the_batch_quietly(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")
        rec = ActivityRecorder(directory=blocker / "activity", enabled=True)
        rec.record("a")
        assert rec.flush() == 0
        assert rec._buffer == []


class TestRecordingEnabled:
    def test_env_off(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "0")
        assert recording_enabled() is False

    def test_env_on(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "1")
        assert recording_enabled() is True

    def test_config_off(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_ACTIVITY", raising=False)
        monkeypatch.setattr("overcode.config.load_config", lambda: {"activity": {"record": False}})
        assert recording_enabled() is False

    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_ACTIVITY", raising=False)
        monkeypatch.setattr("overcode.config.load_config", lambda: {})
        assert recording_enabled() is True


class TestCliRecord:
    GROUPS = {"config", "jobs", "hooks", "sister"}

    @pytest.mark.parametrize("argv,expected", [
        (["launch", "-n", "fred", "-p", "do the thing"], "launch"),
        (["kill", "fred"], "kill"),
        (["config", "show"], "config show"),
        (["config", "--help"], "config"),
        (["send", "fred", "hello"], "send"),
        (["--version"], ""),
        ([], ""),
    ])
    def test_command_path_takes_command_words_only(self, argv, expected):
        assert cli_command_path(argv, self.GROUPS) == expected

    def test_non_overcode_argv_is_not_a_command(self):
        # pytest's argv, seen when a test drives the Typer app in-process
        commands = self.GROUPS | {"launch", "list", "kill"}
        assert cli_command_path(["tests/unit", "-q"], self.GROUPS, commands) == ""
        assert cli_command_path(["kill", "fred"], self.GROUPS, commands) == "kill"

    def test_records_flag_names_not_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "1")
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.setattr("overcode.activity_log.get_activity_dir", lambda: tmp_path)
        record_cli_invocation(["launch", "-n", "fred", "--prompt=do secret", "-p", "x"], self.GROUPS)
        (r,) = _read(tmp_path)
        assert r["kind"] == "cli" and r["cmd"] == "launch" and r["via"] == "cli"
        assert r["flags"] == ["--prompt", "-n", "-p"]
        assert "secret" not in json.dumps(r) and "fred" not in json.dumps(r)

    def test_agent_calls_are_marked_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "1")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "child")
        monkeypatch.setattr("overcode.activity_log.get_activity_dir", lambda: tmp_path)
        record_cli_invocation(["list"], self.GROUPS)
        assert _read(tmp_path)[0]["via"] == "agent"

    def test_internal_commands_are_not_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "1")
        monkeypatch.setattr("overcode.activity_log.get_activity_dir", lambda: tmp_path)
        record_cli_invocation(["hooks", "handle"], self.GROUPS)
        record_cli_invocation(["monitor-daemon", "run"], self.GROUPS)
        assert _read(tmp_path) == []

    def test_off_records_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_ACTIVITY", "0")
        monkeypatch.setattr("overcode.activity_log.get_activity_dir", lambda: tmp_path)
        record_cli_invocation(["list"], self.GROUPS)
        assert _read(tmp_path) == []


class TestIterRecords:
    def test_since_and_until_filter(self, tmp_path):
        rec = ActivityRecorder(directory=tmp_path, enabled=True)
        for i in range(3):
            rec.record("a", i=i)
        base = rec._buffer[0]["t"]
        for i, r in enumerate(rec._buffer):
            r["t"] = base + i * 1000
        rec.flush()
        assert [r["i"] for r in iter_records(since_ms=base + 500, directory=tmp_path)] == [1, 2]
        assert [r["i"] for r in iter_records(until_ms=base + 1500, directory=tmp_path)] == [0, 1]

    def test_skips_bad_lines_and_reads_gzip(self, tmp_path):
        import gzip
        (tmp_path / "2026-01.jsonl").write_text('{"t": 1, "kind": "a"}\nnot json\n')
        with gzip.open(tmp_path / "2025-12.jsonl.gz", "wt") as f:
            f.write('{"t": 0, "kind": "old"}\n')
        assert [r["kind"] for r in iter_records(directory=tmp_path)] == ["old", "a"]

    def test_missing_directory_yields_nothing(self, tmp_path):
        assert list(iter_records(directory=tmp_path / "nope")) == []


class TestActivityPilot:
    """The TUI's chokepoints record keys, actions and dialogs."""

    def _app(self, tmp_path):
        from overcode.tui import SupervisorTUI
        app = SupervisorTUI(tmux_session="test-pilot")
        app._activity = ActivityRecorder("test-pilot", directory=tmp_path, enabled=True)
        return app

    @pytest.mark.asyncio
    async def test_key_action_and_dialog_are_recorded(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            app._flush_activity()
        records = _read(tmp_path)
        kinds = [r["kind"] for r in records]
        assert "key" in kinds
        help_action = [r for r in records if r["kind"] == "action" and r["action"] == "toggle_help"]
        assert help_action and help_action[0]["via"] == "key" and help_action[0]["ok"] is True
        dialogs = [(r["name"], r["phase"]) for r in records if r["kind"] == "dialog"]
        assert ("help", "open") in dialogs and ("help", "cancel") in dialogs

    @pytest.mark.asyncio
    async def test_palette_pick_is_recorded_as_via_palette(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            for ch in "timeline":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            app._flush_activity()
        records = _read(tmp_path)
        picks = [r for r in records if r["kind"] == "action" and r.get("via") == "palette"]
        assert picks and picks[0]["q"] == "timeline" and picks[0]["rank"] == 0
        queries = [r for r in records if r["kind"] == "palette_query"]
        assert queries and queries[-1]["q"] == "timeline" and queries[-1]["n"] >= 1
        # Keys typed into the palette are overcode metadata: kept.
        assert any(r["kind"] == "key" and r["key"] == "t" and r["ctx"].startswith("modal:")
                   for r in records)

    @pytest.mark.asyncio
    async def test_pause_toggle_stops_recording(self, tmp_path):
        app = self._app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_activity_recording()
            await pilot.press("d")
            await pilot.pause()
            app._flush_activity()
        records = _read(tmp_path)
        assert records[-1]["kind"] == "recording" and records[-1]["phase"] == "pause"
        assert not any(r["kind"] == "key" and r["key"] == "d" for r in records)
