"""Tests for view control (#484): the inbox, the ack wait, and the TUI applying commands."""

import json
import os
import threading
import time

import pytest

from overcode.view_control import (
    ControlInbox,
    ack_path,
    append_jsonl,
    control_path,
    read_view_state,
    send_command,
    tui_is_live,
    view_state_path,
    write_json_atomic,
)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
    return "vc-test"


class TestInbox:
    def test_starts_at_eof_so_old_commands_never_replay(self, session):
        append_jsonl(control_path(session), {"id": "old", "verb": "notify"})
        inbox = ControlInbox(session)
        assert inbox.poll() == []
        append_jsonl(control_path(session), {"id": "new", "verb": "notify"})
        assert [c["id"] for c in inbox.poll()] == ["new"]
        assert inbox.poll() == []

    def test_partial_line_waits(self, session):
        inbox = ControlInbox(session)
        path = control_path(session)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write('{"id": "a", "verb": "notify"}\n{"id": "b", "ve')
        assert [c["id"] for c in inbox.poll()] == ["a"]
        with open(path, "a") as f:
            f.write('rb": "sort"}\n')
        assert [c["id"] for c in inbox.poll()] == ["b"]

    def test_bad_lines_and_incomplete_commands_are_skipped(self, session):
        inbox = ControlInbox(session)
        path = control_path(session)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('junk\n{"verb": "x"}\n{"id": "ok", "verb": "sort"}\n')
        assert [c["id"] for c in inbox.poll()] == ["ok"]

    def test_truncated_file_restarts_from_zero(self, session):
        append_jsonl(control_path(session), {"id": "a" * 50, "verb": "notify"})
        inbox = ControlInbox(session)
        control_path(session).write_text('{"id": "b", "verb": "sort"}\n')
        assert [c["id"] for c in inbox.poll()] == ["b"]


class TestSendCommand:
    def test_returns_the_matching_ack(self, session):
        def responder():
            inbox = ControlInbox(session)
            inbox.offset = 0
            for _ in range(50):
                cmds = inbox.poll()
                if cmds:
                    append_jsonl(ack_path(session), {"id": "other", "ok": False})
                    append_jsonl(ack_path(session), {"id": cmds[0]["id"], "ok": True, "result": {"x": 1}})
                    return
                time.sleep(0.02)
        t = threading.Thread(target=responder)
        t.start()
        ack = send_command(session, "sort", {"column": "cost"}, timeout=2, via="cli")
        t.join()
        assert ack["ok"] is True and ack["result"] == {"x": 1}
        (cmd,) = [json.loads(line) for line in control_path(session).read_text().splitlines()]
        assert cmd["verb"] == "sort" and cmd["args"] == {"column": "cost"} and cmd["via"] == "cli"

    def test_times_out_with_a_useful_error(self, session):
        ack = send_command(session, "sort", {}, timeout=0.2)
        assert ack["ok"] is False and "no TUI" in ack["error"]

    def test_via_agent_inside_an_overcode_agent(self, session, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "overagent")
        send_command(session, "notify", {"text": "hi"}, timeout=0.1)
        assert json.loads(control_path(session).read_text())["via"] == "agent"


class TestLiveness:
    def test_fresh_state_from_this_process_is_live(self, session):
        write_json_atomic(view_state_path(session), {"pid": os.getpid(), "updated": time.time()})
        assert tui_is_live(read_view_state(session))

    def test_stale_or_dead_is_not_live(self):
        assert not tui_is_live({"pid": os.getpid(), "updated": time.time() - 60})
        assert not tui_is_live({"pid": 999_999_9, "updated": time.time()})
        assert not tui_is_live(None)


class TestViewControlPilot:
    """The TUI applies commands through its own handlers and acks them."""

    async def _run(self, app, pilot, verb, **args):
        cid = f"c{time.monotonic_ns()}"
        app._run_view_command({"id": cid, "verb": verb, "args": args, "via": "agent"})
        await pilot.pause()
        for line in ack_path(app.tmux_session).read_text().splitlines():
            ack = json.loads(line)
            if ack["id"] == cid:
                return ack
        raise AssertionError("no ack")

    @pytest.mark.asyncio
    async def test_columns_sort_detail_filter_and_errors(self, tmp_path):
        from overcode.tui import SupervisorTUI
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test() as pilot:
            await pilot.pause()

            ack = await self._run(app, pilot, "columns", op="hide", ids=["Cost"], level="med")
            assert ack["ok"], ack
            assert app._prefs.column_config["med"]["cost"] is False

            ack = await self._run(app, pilot, "columns", op="show", ids=["brnch"], level="med")
            assert not ack["ok"] and "did you mean" in ack["error"] and "branch" in ack["error"]

            ack = await self._run(app, pilot, "columns", op="reset", level="med")
            assert ack["ok"] and "med" not in app._prefs.column_config

            ack = await self._run(app, pilot, "detail", level="high")
            assert ack["ok"] and app.SUMMARY_LEVELS[app.summary_level_index] == "high"
            assert app._prefs.summary_detail == "high"

            ack = await self._run(app, pilot, "sort", column="tree")
            assert ack["ok"] and app._prefs.sort_mode == "by_tree"

            ack = await self._run(app, pilot, "filter", tag="backend")
            assert ack["ok"] and app.tag_filter == "backend"
            ack = await self._run(app, pilot, "filter", tag=None)
            assert app.tag_filter is None

            ack = await self._run(app, pilot, "toggle", action="toggle_timeline")
            assert ack["ok"] and ack["result"]["title"]

            ack = await self._run(app, pilot, "toggle", action="toggle_timelin")
            assert not ack["ok"] and "toggle_timeline" in ack["error"]

            ack = await self._run(app, pilot, "focus", agent="nobody")
            assert not ack["ok"]

            ack = await self._run(app, pilot, "point", action="jump_to_attention")
            assert ack["ok"] and ack["result"]["keys"] == ["b"]

            ack = await self._run(app, pilot, "frobnicate")
            assert not ack["ok"] and "unknown verb" in ack["error"]

            ack = await self._run(app, pilot, "sort", colum="x")
            assert not ack["ok"] and "bad arguments" in ack["error"]

            state = read_view_state("test-pilot")
            assert state["level"] == "high" and state["pid"] == os.getpid()
            assert state["sort"]["mode"] == "by_tree"
            assert any(a["action"] == "view:sort" and a["via"] == "agent" for a in state["recent_actions"])

    @pytest.mark.asyncio
    async def test_tick_picks_up_the_inbox(self, tmp_path):
        from overcode.tui import SupervisorTUI
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test() as pilot:
            await pilot.pause()
            append_jsonl(control_path("test-pilot"),
                         {"id": "tick1", "verb": "detail", "args": {"level": "full"}, "via": "cli"})
            app._view_control_tick()
            await pilot.pause()
            assert app.SUMMARY_LEVELS[app.summary_level_index] == "full"
            acks = [json.loads(line) for line in ack_path("test-pilot").read_text().splitlines()]
            assert any(a["id"] == "tick1" and a["ok"] for a in acks)
