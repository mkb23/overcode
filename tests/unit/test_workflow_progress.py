"""How far through its workflow runs a Claude agent is (#498).

The script's ``meta`` (parsed, never run), the run's journal, its final
record and hooks alone, folded into ``SessionDaemonState.workflow_progress``;
the WF cell, its hover and ``overcode show`` drawing it. The capture
replays use the scrubbed spike captures 05 and 06
(docs/design/spike-agent-types/captures), laid out in a temp directory as
Claude writes them beside a transcript.
"""

import json
import os
from pathlib import Path

import pytest

from overcode import swarm
from overcode.swarm_files import SwarmFiles
from overcode.workflow_progress import (
    PHASE_TITLE_CHARS,
    PHASES_KEEP,
    RECENT_RUNS,
    WorkflowFiles,
    active_run,
    describe_run,
    parse_meta,
    progress,
    run_progress,
)

pytestmark = pytest.mark.unit

CAPTURES = Path(__file__).resolve().parents[2] / "docs" / "design" / "spike-agent-types" / "captures"


# ---------------------------------------------------------------------------
# The script's meta
# ---------------------------------------------------------------------------

class TestParseMeta:
    def test_the_captured_scripts(self):
        for name, expect in (("05-workflow-small", {"name": "spike-wf", "phases": ["Ask", "Merge"]}),
                             ("06-workflow-slow-live-watch",
                              {"name": "spike-wf-slow", "phases": ["Sleep", "Join"]})):
            assert parse_meta((CAPTURES / name / "prompt.txt").read_text()) == expect

    def test_the_literal_forms_a_script_may_use(self):
        script = """// a review
export const meta = {
  name: "review-changes",   // trailing comment
  'description': `two
lines`,
  /* a block comment */
  phases: [
    { title: 'Find', detail: "it's \\"quoted\\"" },
    { title: "Ver\\u0069fy", model: 'opus', n: 3, deep: { a: [1, 2.5e3, -4, true, null] } },
    'Report',
  ],
}
phase('Find')
"""
        assert parse_meta(script) == {"name": "review-changes",
                                      "phases": ["Find", "Verify", "Report"]}

    @pytest.mark.parametrize("script", [
        None, 42, "", "phase('x')",
        "export const meta = { name: makeName(), phases: [] }",
        "export const meta = { name: `wf-${n}` }",
        "export const meta = { ...base }",
        "export const meta = { name: 'x', phases: [{ title: 'a' }",
        "export const meta = { name: 'unterminated }",
        "export const meta = 'not an object'",
        "export const meta = { phases: [ { title: 'a' } ] " + "[" * 50,
    ])
    def test_anything_computed_or_broken_is_no_meta(self, script):
        assert parse_meta(script) is None

    def test_name_alone_or_phases_alone(self):
        assert parse_meta("export const meta = { name: 'x', phases: [] }") == {"name": "x"}
        assert parse_meta("export const meta = { phases: [{title: 'A'}] }") == {"phases": ["A"]}

    def test_bounded(self):
        phases = ", ".join(f"{{title: 'phase {i} ' + ''}}" for i in range(3))
        assert parse_meta(f"export const meta = {{ phases: [{phases}] }}") is None  # computed
        many = ", ".join(f"{{title: 'p{i}'}}" for i in range(PHASES_KEEP + 5))
        got = parse_meta(f"export const meta = {{ name: '{'n' * 300}', phases: [{many}] }}")
        assert len(got["phases"]) == PHASES_KEEP
        assert len(got["name"]) == 80 and got["name"].endswith("…")
        long = parse_meta("export const meta = { phases: [{title: '" + "t" * 99 + "'}] }")
        assert len(long["phases"][0]) == PHASE_TITLE_CHARS


# ---------------------------------------------------------------------------
# The hook side keeps the declared phases
# ---------------------------------------------------------------------------

SCRIPT = ("export const meta = { name: 'review-changes', description: 'd',\n"
          "  phases: [{title: 'Find'}, {title: 'Verify'}, {title: 'Report'}] }\nphase('Find')")


def _launch(tool_input, response=None, sw=None):
    response = response or {"status": "async_launched", "taskId": "w1", "runId": "wf_1",
                            "workflowName": "review-changes",
                            "scriptPath": "/s/workflows/scripts/review-changes-wf_1.js"}
    return swarm.apply_parent_event({}, sw or {}, "PostToolUse", "Workflow", tool_input,
                                    {"tool_response": response}, 5.0)


class TestHookSide:
    def test_the_script_s_phases_are_kept_with_the_run(self):
        _, sw = _launch({"script": SCRIPT})
        run = sw["workflows"]["w1"]
        assert run["phases"] == ["Find", "Verify", "Report"]
        assert run["name"] == "review-changes" and run["run_id"] == "wf_1"
        assert "script_path" not in run

    def test_a_named_workflow_keeps_where_its_script_was_saved(self):
        _, sw = _launch({"name": "review"})
        run = sw["workflows"]["w1"]
        assert "phases" not in run
        assert run["script_path"] == "/s/workflows/scripts/review-changes-wf_1.js"

    def test_script_path_wins_over_script_as_in_the_tool(self):
        _, sw = _launch({"script": SCRIPT, "scriptPath": "/edited.js"})
        assert "phases" not in sw["workflows"]["w1"]
        assert sw["workflows"]["w1"]["script_path"]

    def test_the_meta_name_stands_in_for_a_missing_workflow_name(self):
        _, sw = _launch({"script": SCRIPT}, {"status": "async_launched", "taskId": "w1"})
        assert sw["workflows"]["w1"]["name"] == "review-changes"

    def test_an_unparsable_script_still_records_the_run(self):
        _, sw = _launch({"script": "export const meta = { name: f() }"})
        assert sw["workflows"]["w1"]["status"] == "running"
        assert "phases" not in sw["workflows"]["w1"]


# ---------------------------------------------------------------------------
# One run's progress
# ---------------------------------------------------------------------------

def _journal(*events):
    out = [{"type": "launched"}]
    for kind, agent, phase in events:
        if kind == "started":
            out.append({"type": "started", "agentId": agent, "label": agent, "phase": phase})
        else:
            out.append({"type": "result", "agentId": agent, "result": "r"})
    return out


RUN = {"name": "review-changes", "status": "running", "started_at": 100.0, "run_id": "wf_1",
       "phases": ["Find", "Verify", "Report"]}


class TestRunProgress:
    def test_journal_counts_per_phase_and_the_phase_it_is_in(self):
        journal = _journal(("started", "a1", "Find"), ("started", "a2", "Find"),
                           ("result", "a1", None), ("result", "a2", None),
                           ("started", "b1", "Verify"), ("started", "b2", "Verify"),
                           ("started", "b3", "Verify"), ("result", "b2", None))
        got = run_progress(RUN, journal=journal)
        assert got == {
            "name": "review-changes", "status": "running", "started_at": 100.0,
            "source": "journal", "started": 5, "done": 3, "failed": 0,
            "phase": "Verify", "phase_index": 2, "phase_count": 3,
            "phases": [{"title": "Find", "started": 2, "done": 2},
                       {"title": "Verify", "started": 3, "done": 1},
                       {"title": "Report", "started": 0, "done": 0}],
        }

    def test_an_agent_started_again_counts_once(self):
        journal = _journal(("started", "a1", "Find"), ("started", "a1", "Find"))
        assert run_progress(RUN, journal=journal)["started"] == 1

    def test_undeclared_phases_come_from_the_journal_with_no_fraction(self):
        run = dict(RUN, phases=None)
        got = run_progress(run, journal=_journal(("started", "a1", "Scan"),
                                                 ("started", "b1", "Fix")))
        assert got["phase"] == "Fix"
        assert "phase_index" not in got and "phase_count" not in got
        assert [p["title"] for p in got["phases"]] == ["Scan", "Fix"]

    def test_a_phase_the_script_did_not_declare_is_listed_after_those_it_did(self):
        got = run_progress(RUN, journal=_journal(("started", "a1", "Find"),
                                                 ("started", "x1", "Extra")))
        assert [p["title"] for p in got["phases"]] == ["Find", "Verify", "Report", "Extra"]
        assert got["phase"] == "Extra" and "phase_index" not in got

    def test_the_final_record_is_exact_failures_included(self):
        final = {"phases": [{"title": "Find"}, {"title": "Verify"}],
                 "workflowProgress": [
                     {"type": "workflow_phase", "index": 1, "title": "Find"},
                     {"type": "workflow_agent", "phaseTitle": "Find", "state": "done"},
                     {"type": "workflow_agent", "phaseTitle": "Verify", "state": "done"},
                     {"type": "workflow_agent", "phaseTitle": "Verify", "state": "failed"}]}
        run = dict(RUN, status="failed", ended_at=200.0, phases=None)
        got = run_progress(run, journal=_journal(("started", "a1", "Find")), final=final)
        assert got["source"] == "final"
        assert (got["started"], got["done"], got["failed"]) == (3, 3, 1)
        assert got["phases"] == [{"title": "Find", "started": 1, "done": 1},
                                 {"title": "Verify", "started": 2, "done": 2, "failed": 1}]
        assert (got["phase"], got["phase_index"], got["phase_count"]) == ("Verify", 2, 2)

    def test_hooks_alone_give_counts_and_no_phases(self):
        got = run_progress(RUN, hook_counts=(4, 1))
        assert got["source"] == "hooks"
        assert (got["started"], got["done"], got["phase_count"]) == (4, 1, 3)
        assert "phases" not in got and "phase" not in got

    def test_a_saved_script_s_meta_names_the_phases(self):
        run = {"status": "running", "run_id": "wf_1"}
        got = run_progress(run, journal=_journal(("started", "a1", "B")),
                           script_meta={"name": "named", "phases": ["A", "B"]})
        assert got["name"] == "named" and (got["phase_index"], got["phase_count"]) == (2, 2)

    def test_it_holds_still(self):
        journal = _journal(("started", "a1", "Find"))
        assert run_progress(RUN, journal=journal) == run_progress(RUN, journal=journal)


def _state(runs, done=None, live=None, transcript=None):
    sw = {"workflows": runs, "done": done or {}}
    if transcript:
        sw["transcript"] = transcript
    return {"event": "Stop", "timestamp": 1.0, "subagents": live or {}, "swarm": sw}


class TestProgress:
    def test_no_runs_no_progress(self):
        assert progress(None) is None
        assert progress(_state({})) is None

    def test_hooks_attribute_finished_agents_by_run_and_live_ones_to_the_newest(self):
        runs = {"w1": {"status": "ok", "run_id": "wf_1", "name": "old"},
                "w2": {"status": "running", "run_id": "wf_2", "name": "new"}}
        done = {"a1": {"kind": "workflow", "run": "wf_1", "result": "ok"},
                "a2": {"kind": "workflow", "run": "wf_2", "result": "ok"},
                "s1": {"kind": "subagent", "result": "ok"}}
        live = {"a3": {"kind": "workflow"}, "a4": {"kind": "workflow"}}
        got = progress(_state(runs, done, live))
        assert [(r["name"], r["started"], r["done"]) for r in got] == [("old", 1, 1),
                                                                       ("new", 3, 1)]
        assert active_run(got)["name"] == "new"

    def test_running_runs_and_the_newest_finished_ones(self):
        runs = {f"w{i}": {"status": "ok", "name": f"r{i}"} for i in range(6)}
        runs["w1"]["status"] = "running"
        got = progress(_state(runs))
        assert [r["name"] for r in got] == ["r1"] + [f"r{i}" for i in range(6 - RECENT_RUNS, 6)]

    def test_no_active_run_once_all_finished(self):
        assert active_run(progress(_state({"w1": {"status": "ok"}}))) is None
        assert active_run(None) is None


# ---------------------------------------------------------------------------
# The files: captures replayed, journals read on change only
# ---------------------------------------------------------------------------

def _lay_out(capture: str, root: Path, journal_lines=None) -> str:
    """The capture's files under ``root`` as Claude lays them out; the parent's transcript path."""
    files = CAPTURES / capture / "files"
    sid = None
    for f in sorted(files.iterdir()):
        parts = f.name.split("__")
        sid = parts[0]
        dest = root.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = f.read_text()
        if parts[-1] == "journal.jsonl" and journal_lines is not None:
            text = "".join(text.splitlines(keepends=True)[:journal_lines])
        if parts[-1].startswith("wf_") and journal_lines is not None:
            continue  # the final record: only once the run completed
        dest.write_text(text)
    return str(root / f"{sid}.jsonl")


def _replay(capture: str, until: float = float("inf")):
    """The parent's hook state after the capture's hook events up to ``until`` (s after the first)."""
    subs, sw = {}, {}
    events = [json.loads(line) for line in (CAPTURES / capture / "hooks.jsonl").read_text().splitlines()]
    t0 = events[0]["_t"]
    for data in events:
        if data["_t"] - t0 > until or data["_event"] == "SessionEnd":
            break  # (SessionEnd clears the record: the conversation is over)
        event, now = data["_event"], data["_t"]
        if data.get("agent_id"):
            subs, sw = swarm.apply_subagent_event(subs, sw, event, data["agent_id"], data, now)
        else:
            subs, sw = swarm.apply_parent_event(subs, sw, event, data.get("tool_name"),
                                                data.get("tool_input"), data, now)
    return {"event": "Stop", "timestamp": 1.0, "subagents": subs, "swarm": sw}


class TestCaptures:
    def test_mid_run_from_the_journal(self, tmp_path):
        # 10 s in: the three sleepers started, one finished (journal's first 5 lines)
        state = _replay("06-workflow-slow-live-watch", until=12.0)
        state["swarm"]["transcript"] = _lay_out("06-workflow-slow-live-watch", tmp_path,
                                                journal_lines=5)
        (run,) = WorkflowFiles().of("id-p", state)
        assert run["name"] == "spike-wf-slow" and run["status"] == "running"
        assert run["source"] == "journal"
        assert (run["phase"], run["phase_index"], run["phase_count"]) == ("Sleep", 1, 2)
        assert (run["started"], run["done"]) == (3, 1)
        assert run["phases"] == [{"title": "Sleep", "started": 3, "done": 1},
                                 {"title": "Join", "started": 0, "done": 0}]

    def test_mid_run_from_hooks_alone(self, tmp_path):
        state = _replay("06-workflow-slow-live-watch", until=12.0)
        state["swarm"]["transcript"] = str(tmp_path / "nothing-here.jsonl")
        (run,) = WorkflowFiles().of("id-p", state)
        assert run["source"] == "hooks"
        assert (run["started"], run["done"], run["phase_count"]) == (3, 1, 2)

    def test_finished_from_the_final_record(self, tmp_path):
        state = _replay("06-workflow-slow-live-watch")
        state["swarm"]["transcript"] = _lay_out("06-workflow-slow-live-watch", tmp_path)
        (run,) = WorkflowFiles().of("id-p", state)
        assert run["status"] == "ok" and run["source"] == "final"
        assert (run["started"], run["done"], run["failed"]) == (4, 4, 0)
        assert run["phases"] == [{"title": "Sleep", "started": 3, "done": 3},
                                 {"title": "Join", "started": 1, "done": 1}]
        assert active_run([run]) is None

    def test_the_small_capture(self, tmp_path):
        state = _replay("05-workflow-small")
        state["swarm"]["transcript"] = _lay_out("05-workflow-small", tmp_path)
        (run,) = WorkflowFiles().of("id-p", state)
        assert (run["name"], run["started"], run["done"]) == ("spike-wf", 3, 3)
        assert [p["title"] for p in run["phases"]] == ["Ask", "Merge"]

    def test_a_named_run_s_saved_script_is_read_once(self, tmp_path):
        script = tmp_path / "saved.js"
        script.write_text(SCRIPT)
        state = _state({"w1": {"status": "running", "run_id": "wf_1", "script_path": str(script)}},
                       transcript=str(tmp_path / "t.jsonl"))
        files = WorkflowFiles()
        assert files.of("id-p", state)[0]["phase_count"] == 3
        reads = files.files.reads
        files.of("id-p", state)
        assert files.files.reads == reads


class TestJournalReads:
    def _setup(self, tmp_path):
        base = tmp_path / "s"
        journal = base / "subagents" / "workflows" / "wf_1" / "journal.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_text("")
        state = _state({"w1": dict(RUN)}, transcript=str(tmp_path / "s.jsonl"))
        return journal, state

    def _append(self, journal, text):
        with open(journal, "a") as f:
            f.write(text)
        # A distinct mtime for each write, whatever the filesystem's resolution
        st = os.stat(journal)
        os.utime(journal, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def test_read_on_change_only_from_where_it_stopped(self, tmp_path):
        journal, state = self._setup(tmp_path)
        files = WorkflowFiles()
        assert files.of("id-p", state)[0]["started"] == 0
        assert files.changed() == set()
        line = json.dumps({"type": "started", "agentId": "a1", "phase": "Find"})
        self._append(journal, line + "\n" + line[:10])  # a line still being written
        assert files.changed() == {"id-p"}
        assert files.changed() == set()  # each move reported once
        assert files.of("id-p", state)[0]["started"] == 1
        reads = files.files.reads
        for _ in range(5):  # quiet: no read
            files.of("id-p", state)
        assert files.files.reads == reads
        self._append(journal, line[10:] + "\n")
        # The half line completed: it is one more event (the same agent again here)
        got = files.of("id-p", state)[0]
        assert got["started"] == 1 and files.files.reads == reads + 1

    def test_a_finished_run_is_not_watched(self, tmp_path):
        journal, state = self._setup(tmp_path)
        state["swarm"]["workflows"]["w1"]["status"] = "ok"
        files = WorkflowFiles()
        files.of("id-p", state)
        self._append(journal, "{}\n")
        assert files.changed() == set()

    def test_a_journal_that_appears_is_a_change(self, tmp_path):
        journal, state = self._setup(tmp_path)
        journal.unlink()
        files = WorkflowFiles()
        assert files.of("id-p", state)[0]["source"] == "hooks"
        assert files.changed() == set()
        journal.write_text(json.dumps({"type": "started", "agentId": "a1", "phase": "Find"}) + "\n")
        assert files.changed() == {"id-p"}
        assert files.of("id-p", state)[0]["source"] == "journal"

    def test_forget(self, tmp_path):
        journal, state = self._setup(tmp_path)
        files = WorkflowFiles()
        files.of("id-p", state)
        files.forget(set())
        self._append(journal, "{}\n")
        assert files.changed() == set()

    def test_the_swarm_publisher_s_journal_cache_still_works_incrementally(self, tmp_path):
        journal, _ = self._setup(tmp_path)
        files = SwarmFiles()
        line = json.dumps({"type": "started", "agentId": "a1", "phase": "Find"}) + "\n"
        self._append(journal, line)
        assert len(files.journal(str(journal))) == 1
        self._append(journal, line.replace("a1", "a2"))
        assert [e["agentId"] for e in files.journal(str(journal))] == ["a1", "a2"]
        journal.write_text(line)  # shrank (rewritten): read from the start
        assert [e["agentId"] for e in files.journal(str(journal))] == ["a1"]


# ---------------------------------------------------------------------------
# The published state
# ---------------------------------------------------------------------------

def test_the_state_round_trips():
    from overcode.monitor_daemon_state import SessionDaemonState
    runs = [run_progress(RUN, journal=_journal(("started", "a1", "Find")))]
    state = SessionDaemonState(session_id="s", name="n", workflow_progress=runs)
    assert SessionDaemonState.from_dict(state.to_dict()).workflow_progress == runs
    assert SessionDaemonState.from_dict({"session_id": "s"}).workflow_progress is None


def test_the_daemon_publishes_progress_on_change_only(tmp_path, monkeypatch):
    """Whole ticks: the journal is read when it moves, never on a quiet tick;
    the published value holds still in between."""
    from datetime import datetime

    from overcode.session_manager import SessionManager
    from tests.daemon_tick_harness import (FrozenClock, ScriptedDetector, make_daemon,
                                           run_ticks, seed_sessions, seed_steady_state,
                                           set_sync_stamps)

    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    start = datetime(2026, 9, 23, 12, 0, 0)
    journal = tmp_path / "t" / "subagents" / "workflows" / "wf_1" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("")
    hook_state = _state({"w1": dict(RUN)}, transcript=str(tmp_path / "t.jsonl"))

    class Detector(ScriptedDetector):
        def get_swarm_state(self, name):
            return hook_state if name == "agent-00" else None

        def get_swarm_rollup(self, name):
            return swarm.rollup(hook_state, start.timestamp()) if name == "agent-00" else None

    sm = SessionManager(state_dir=home / ".overcode" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, 2, "agents", tmp_path / "work", start)
    detector = Detector(lambda tick, s: ("running", "Working", "pane"))
    daemon = make_daemon(home / ".overcode", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, start)
    published = []

    def between(tick):
        set_sync_stamps(daemon, clock.now)
        state = next(s for s in daemon.state.sessions if s.name == "agent-00")
        published.append((state.workflow_progress[0]["started"], daemon.workflow_files.files.reads))
        if tick == 3:
            with open(journal, "a") as f:
                f.write(json.dumps({"type": "started", "agentId": "a1", "phase": "Verify"}) + "\n")
            st = os.stat(journal)
            os.utime(journal, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    with FrozenClock(start).installed() as clock:
        run_ticks(daemon, detector, clock, 8, between_ticks=between)
    other = next(s for s in daemon.state.sessions if s.name == "agent-01")
    assert other.workflow_progress is None
    assert [p[0] for p in published] == [0, 0, 0, 0, 1, 1, 1, 1]
    # One read when it moved; none on the quiet ticks either side
    assert published[4][1] == published[3][1] + 1
    assert published[-1][1] == published[4][1]
    assert published[0][1] == published[3][1]
    state = next(s for s in daemon.state.sessions if s.name == "agent-00")
    assert (state.workflow_progress[0]["phase"], state.workflow_progress[0]["phase_index"]) == (
        "Verify", 2)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _ctx(**kw):
    from tests.unit.test_summary_columns import _make_ctx
    return _make_ctx(**kw)


RUNNING = {"name": "review-changes", "status": "running", "started": 12, "done": 7,
           "failed": 0, "phase": "Verify", "phase_index": 2, "phase_count": 3,
           "phases": [{"title": "Find", "started": 3, "done": 3},
                      {"title": "Verify", "started": 9, "done": 4},
                      {"title": "Report", "started": 0, "done": 0}],
           "source": "journal"}
FINISHED = {"name": "lint-sweep", "status": "failed", "started": 4, "done": 4, "failed": 1,
            "source": "final", "phases": [{"title": "Lint", "started": 4, "done": 4, "failed": 1}]}


class TestCell:
    def _text(self, **kw):
        from overcode.columns.render_performance import render_workflow
        return render_workflow(_ctx(**kw))[0][0]

    def test_the_running_run(self):
        assert self._text(workflow_progress=[FINISHED, RUNNING]) == " 🧩 review-changes · 2/3 · 7/12✅"

    def test_the_name_is_cut_by_detail_level(self):
        assert self._text(workflow_progress=[RUNNING], summary_detail="med") == (
            " 🧩 review-chan… · 2/3 · 7/12✅")
        # None at low: the hover names it
        assert self._text(workflow_progress=[RUNNING], summary_detail="low") == " 🧩 2/3 · 7/12✅"

    def test_no_declared_phases_shows_the_phase_s_title(self):
        run = dict(RUNNING, phase_index=None, phase_count=None, phase="Verification-pass")
        assert self._text(workflow_progress=[run]) == (
            " 🧩 review-changes · Verificat… · 7/12✅")

    def test_hooks_only_no_phase(self):
        run = {"name": "wf", "status": "running", "started": 3, "done": 1, "source": "hooks"}
        assert self._text(workflow_progress=[run]) == " 🧩 wf · 1/3✅"

    def test_a_second_running_run(self):
        assert self._text(workflow_progress=[RUNNING, dict(RUNNING, name="b")]).endswith(" +1")

    def test_the_placeholder_has_the_value_s_shape(self):
        assert self._text(workflow_progress=None) == " 🧩  -"
        assert self._text(workflow_progress=[FINISHED]) == " 🧩  -"

    def test_ascii(self):
        assert self._text(workflow_progress=[RUNNING], emoji_free=True) == (
            " Wk review-changes · 2/3 · 7/12ok")

    def test_plain_for_overcode_show(self):
        from overcode.columns.render_performance import render_workflow_plain
        assert render_workflow_plain(_ctx(workflow_progress=None)) is None
        text = render_workflow_plain(_ctx(workflow_progress=[FINISHED, RUNNING]))
        assert text.split("\n") == [
            "review-changes · running · phase 2/3 Verify · 7/12 agents finished",
            "  Find  3/3 finished",
            "  Verify  4/9 finished",
            "  Report  not started",
            "lint-sweep · ❌ failed · 4/4 agents finished, 1 failed",
            "  Lint  4/4 finished, 1 failed",
        ]

    def test_hover(self):
        from overcode.columns.hover import hover_subagents, hover_workflow
        assert hover_workflow(_ctx(workflow_progress=None)) is None
        text = hover_workflow(_ctx(workflow_progress=[FINISHED, RUNNING])).plain
        assert text.startswith("Workflows\n🧩  review-changes · running · phase 2/3 Verify")
        assert "  Verify  4/9 finished" in text and "❌  lint-sweep" in text
        rollup = {"total": 16, "running": 5, "ok": 11, "workflows": 2, "workflows_running": 1}
        sub = hover_subagents(_ctx(swarm_rollup=rollup, workflow_progress=[RUNNING])).plain
        assert "2 workflow runs, 1 running" in sub and "phase 2/3 Verify" in sub

    def test_hooks_only_detail_says_so(self):
        run = {"name": "wf", "status": "running", "started": 3, "done": 1, "source": "hooks"}
        assert describe_run(run)[-1] == "  (from hooks only: no per-phase counts)"

    def test_the_column_shows_only_while_a_run_is_going(self):
        from overcode.columns.context import FleetRow, compute_fleet_flags
        from overcode.summary_columns import COLUMNS_BY_ID
        col = COLUMNS_BY_ID["workflow"]
        assert col.description and col.header == "WF"
        assert not compute_fleet_flags([FleetRow(session=object())]).any_has_workflow
        flags = compute_fleet_flags([FleetRow(session=object(), has_workflow=True)])
        assert flags.any_has_workflow
        assert col.visible(_ctx(fleet=flags)) and not col.visible(_ctx())

    def test_sort_furthest_along_first(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        key = COLUMNS_BY_ID["workflow"].sort_key
        assert key(_ctx()) is None
        assert key(_ctx(workflow_progress=[RUNNING])) == (2, 7)
