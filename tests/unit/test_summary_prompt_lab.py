"""Tests for editable summarizer prompts and the prompt lab (#491)."""

import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overcode import summarizer_prompts as sp
from overcode.prompt_lab import LabSample, PromptLabRunner, pick_samples


@pytest.fixture
def overcode_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Prompt files
# ---------------------------------------------------------------------------

class TestPromptFiles:

    def test_default_when_no_file(self, overcode_dir):
        assert sp.load_prompt("short") == sp.DEFAULT_PROMPT_SHORT
        assert sp.load_prompt("context") == sp.DEFAULT_PROMPT_CONTEXT
        assert not sp.is_customised("short")
        # Reading never creates the file
        assert not (overcode_dir / "prompts").exists()

    def test_saved_file_wins_and_edits_are_picked_up(self, overcode_dir):
        path = sp.save_prompt("short", "first {pane_content}")
        assert path == overcode_dir / "prompts" / "summary-short.md"
        assert sp.load_prompt("short") == "first {pane_content}"
        assert sp.is_customised("short")
        # An edit from outside (another size) is seen on the next load
        path.write_text("second, longer {pane_content}")
        assert sp.load_prompt("short") == "second, longer {pane_content}"
        # The other mode is unaffected
        assert sp.load_prompt("context") == sp.DEFAULT_PROMPT_CONTEXT

    def test_blank_file_falls_back_to_default(self, overcode_dir):
        sp.save_prompt("context", "   \n")
        assert sp.load_prompt("context") == sp.DEFAULT_PROMPT_CONTEXT

    def test_reset_removes_the_file(self, overcode_dir):
        sp.save_prompt("short", "x {pane_content}")
        sp.reset_prompt("short")
        assert sp.load_prompt("short") == sp.DEFAULT_PROMPT_SHORT
        sp.reset_prompt("short")  # idempotent

    def test_unknown_mode_is_rejected(self, overcode_dir):
        with pytest.raises(ValueError):
            sp.prompt_path("long")


class TestRenderPrompt:

    def test_fills_known_placeholders(self):
        out = sp.render_prompt(
            "{lines} lines:\n{pane_content}\nwas: {previous_summary} ({status})",
            pane_content="$ make", lines=40, previous_summary="", status="running",
        )
        assert out == "40 lines:\n$ make\nwas: (no previous summary) (running)"

    def test_other_braces_are_left_alone(self):
        # A hand-edited prompt quoting JSON must not raise like str.format
        out = sp.render_prompt(
            'reply as {"summary": "..."} {unknown} {\n{pane_content}',
            pane_content="x", lines=1, previous_summary="p", status="s",
        )
        assert out == 'reply as {"summary": "..."} {unknown} {\nx'

    def test_braces_in_pane_content_are_not_expanded(self):
        out = sp.render_prompt(
            "{pane_content}|{status}",
            pane_content="echo {status} {lines}", lines=1, previous_summary="", status="ok",
        )
        assert out == "echo {status} {lines}|ok"

    def test_default_prompts_render_the_same_as_before(self):
        # The old code used str.format on these; the result must not change.
        kwargs = dict(pane_content="PANE", lines=200, previous_summary="prev", status="running")
        for template in sp.DEFAULT_PROMPTS.values():
            assert sp.render_prompt(template, **kwargs) == template.format(**kwargs)

    def test_warnings(self):
        assert sp.unknown_placeholders("{pane_content} {statsu} {lines}") == ["statsu"]
        assert sp.missing_pane_content("no terminal here")
        assert not sp.missing_pane_content("{pane_content}")


class TestClientUsesSavedPrompt:

    def _client(self):
        from overcode.summarizer_client import SummarizerClient
        client = SummarizerClient.__new__(SummarizerClient)
        client.api_url, client.model, client.api_key = "http://test", "test", "k"
        client._available = True
        return client

    def test_saved_prompt_is_sent(self, overcode_dir):
        sp.save_prompt("short", "CUSTOM PROMPT: {pane_content}")
        client = self._client()
        with patch.object(client, "_call_openai", return_value="ok") as call:
            client.summarize(pane_content="PANE", previous_summary="", current_status="running")
        assert call.call_args[0][0] == "CUSTOM PROMPT: PANE"

    def test_explicit_template_overrides_the_file(self, overcode_dir):
        sp.save_prompt("short", "FILE {pane_content}")
        client = self._client()
        with patch.object(client, "_call_openai", return_value="ok") as call:
            client.summarize(pane_content="PANE", previous_summary="", current_status="running",
                             prompt_template="DRAFT {pane_content}")
        assert call.call_args[0][0] == "DRAFT PANE"


# ---------------------------------------------------------------------------
# Lab engine
# ---------------------------------------------------------------------------

def _session(sid, state="running"):
    return SimpleNamespace(id=sid, stats=SimpleNamespace(current_state=state))


class TestPickSamples:

    def test_focused_first_then_display_order_capped(self):
        sessions = [_session(str(i)) for i in range(10)]
        picked = pick_samples(sessions, "7", limit=6)
        assert [s.id for s in picked] == ["7", "0", "1", "2", "3", "4"]

    def test_terminated_are_skipped(self):
        sessions = [_session("a", "terminated"), _session("b"), _session("c", "waiting_user")]
        assert [s.id for s in pick_samples(sessions, "a")] == ["b", "c"]


class _FakeClient:
    model = "gpt-4o-mini"

    def __init__(self, log, reply="doing things"):
        self.log = log
        self.reply = reply
        self.last_input_tokens = 0
        self.last_output_tokens = 0

    def summarize(self, **kwargs):
        self.log.append(kwargs)
        self.last_input_tokens, self.last_output_tokens = 100, 5
        return self.reply


def _runner(panes, log, reply="doing things", costs=None):
    return PromptLabRunner(
        capture=lambda window: panes.get(window),
        make_client=lambda: _FakeClient(log, reply),
        lines=40,
        on_cost=(costs.append if costs is not None else None),
    )


SAMPLES = [
    LabSample(session_id="1", name="alpha", window="w1", status="running", live="old one"),
    LabSample(session_id="2", name="beta", window="w2", status="waiting_user"),
]


class TestRunner:

    def test_first_pass_runs_every_sample_with_the_draft(self):
        log, costs = [], []
        runner = _runner({"w1": "pane one", "w2": "pane two"}, log, costs=costs)
        assert runner.run("short", "DRAFT {pane_content}", SAMPLES) == 2
        assert {c["pane_content"] for c in log} == {"pane one", "pane two"}
        assert all(c["prompt_template"] == "DRAFT {pane_content}" for c in log)
        assert all(c["mode"] == "short" and c["max_tokens"] == 50 for c in log)
        # The live summary is the prompt's previous summary, as it would be live
        assert next(c for c in log if c["pane_content"] == "pane one")["previous_summary"] == "old one"
        assert runner.results["1"].text == "doing things"
        assert runner.results["1"].input_tokens == 100
        assert runner.calls == 2 and len(costs) == 2 and runner.spent_usd > 0

    def test_unchanged_inputs_are_not_rerun(self):
        log = []
        panes = {"w1": "pane one", "w2": "pane two"}
        runner = _runner(panes, log)
        runner.run("short", "T {pane_content}", SAMPLES)
        assert runner.run("short", "T {pane_content}", SAMPLES) == 0
        # One agent's pane moves on: only it is re-run
        panes["w1"] = "pane one, later"
        assert runner.run("short", "T {pane_content}", SAMPLES) == 1
        # A new draft re-runs everyone
        assert runner.run("short", "T2 {pane_content}", SAMPLES) == 2
        # So does a mode switch
        assert runner.run("context", "T2 {pane_content}", SAMPLES) == 2
        assert len(log) == 7

    def test_failed_calls_are_retried(self):
        log = []
        runner = _runner({"w1": "pane"}, log, reply=None)
        runner.run("short", "T", SAMPLES[:1])
        assert runner.results["1"].error
        assert runner.run("short", "T", SAMPLES[:1]) == 1

    def test_agents_without_pane_text_are_skipped(self):
        log = []
        runner = _runner({"w1": ""}, log)
        assert runner.run("short", "T", SAMPLES) == 0

    def test_a_pass_in_flight_is_not_doubled(self):
        gate = threading.Event()

        class Slow(_FakeClient):
            def summarize(self, **kwargs):
                gate.wait(5)
                return "ok"

        runner = PromptLabRunner(capture=lambda w: "pane", make_client=lambda: Slow([]), lines=40)
        t = threading.Thread(target=runner.run, args=("short", "T", SAMPLES[:1]))
        t.start()
        for _ in range(100):
            if runner.busy:
                break
            threading.Event().wait(0.01)
        assert runner.run("short", "T", SAMPLES[:1]) == 0
        gate.set()
        t.join(5)
        assert runner.results["1"].text == "ok"


# ---------------------------------------------------------------------------
# The dialog, driven through the real TUI
# ---------------------------------------------------------------------------

from tests.unit.test_command_palette import _ready, tui  # noqa: E402,F401 (fixture)


def _lab(app):
    from overcode.tui_widgets import SummaryPromptLab
    return app.query_one("#summary-prompt-lab", SummaryPromptLab)


@pytest.mark.asyncio
class TestLabInTUI:

    async def _open(self, pilot):
        await _ready(pilot)
        with patch("overcode.summarizer_client.SummarizerClient.is_available", return_value=False):
            pilot.app.action_open_summary_prompt_lab()
        await pilot.pause()
        return _lab(pilot.app)

    async def test_opens_on_the_short_prompt_with_the_editor_focused(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            lab = await self._open(pilot)
            assert lab.has_class("visible")
            assert tui.focused is lab.editor
            assert lab.mode == "short" and lab.draft == sp.DEFAULT_PROMPT_SHORT
            assert not lab.dirty

    async def test_typing_then_ctrl_s_saves_and_q_does_not_quit(self, tui, tmp_path):
        async with tui.run_test(size=(120, 40)) as pilot:
            lab = await self._open(pilot)
            await pilot.press("ctrl+a")  # line start
            await pilot.press("q", "!", " ")
            await pilot.pause()
            assert lab.draft.startswith("q! ")
            assert lab.dirty
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not lab.dirty
            saved = (tmp_path / "prompts" / "summary-short.md").read_text()
            assert saved.startswith("q! What is the agent doing")
            assert sp.load_prompt("short") == saved

    async def test_esc_with_unsaved_edits_asks_first(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            lab = await self._open(pilot)
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert lab.has_class("visible")  # warned, still open
            await pilot.press("escape")
            await pilot.pause()
            assert not lab.has_class("visible")
            assert not (sp.prompt_path("short")).exists()

    async def test_ctrl_t_switches_to_the_context_prompt(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            lab = await self._open(pilot)
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert lab.mode == "context"
            assert lab.draft == sp.DEFAULT_PROMPT_CONTEXT

    async def test_saving_the_default_text_removes_the_file(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            sp.save_prompt("short", "custom {pane_content}")
            lab = await self._open(pilot)
            assert lab.draft == "custom {pane_content}"
            await pilot.press("ctrl+l")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not sp.prompt_path("short").exists()
            assert not lab.dirty

    async def test_listed_in_the_palette(self, tui):
        from overcode.command_palette import COMMANDS
        assert any(c.action == "open_summary_prompt_lab" for c in COMMANDS)
