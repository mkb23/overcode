"""GrokStatsReader against fixture session dirs built from the real shapes.

The ``updates.jsonl``/``summary.json``/``prompt_history.jsonl`` shapes below
are verbatim (field names, nesting) from a real Grok Build v1.0.5 session
captured under ``~/.grok/sessions`` (the "xway" project's 413-message
session referenced in ``grok_stats.py``'s module docstring), so the fixture
drifts only when grok's on-disk schema does — at which point
``TestSchemaDrift`` documents what the reader does about it.

Two things this fixture encodes deliberately, both determined empirically
against that real session before this reader was written (see
``grok_stats.py``'s module docstring for the full account):

* ``turn_completed.usage`` objects are summed across the file, not "latest
  wins" — the fixture's two turns use different, non-monotonic token counts
  precisely so a "take the last one" implementation would fail the sum
  assertions here.
* ``costUsdTicks`` is nano-dollars (1e9 ticks per USD).
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends.grok_stats import (
    GrokStatsReader,
    encode_cwd,
    schema_findings,
    sessions_root,
)

LAUNCH = datetime(2026, 8, 18, 16, 54, 0)
PROJECT_DIR = "/Users/mike/Code/xway"
SID = "01a015cb-a815-7d92-87a8-d3b58b5a1c2f"
OTHER_SID = "01a015de-468d-7ac2-a049-c8513bbeb947"


def update_envelope(session_update: dict, *, timestamp=1787072216, meta=None):
    params = {"sessionId": SID, "update": session_update}
    if meta is not None:
        params["_meta"] = meta
    return {"timestamp": timestamp, "method": "session/update", "params": params}


def turn_completed(
    input_tokens, output_tokens, cached_read=0, reasoning=0,
    cache_creation=0, cost_ticks=0, num_turns=1, timestamp=1787072216,
    prompt_id="prompt-1",
):
    return update_envelope(
        {
            "sessionUpdate": "turn_completed",
            "prompt_id": prompt_id,
            "stop_reason": "end_turn",
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
                "cachedReadTokens": cached_read,
                "cacheCreationTokens": cache_creation,
                "reasoningTokens": reasoning,
                "modelCalls": num_turns,
                "apiDurationMs": 1000,
                "costUsdTicks": cost_ticks,
                "numTurns": num_turns,
            },
        },
        timestamp=timestamp,
    )


def context_update(total_tokens, timestamp=1787072216):
    return update_envelope(
        {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "..."}},
        timestamp=timestamp,
        meta={"totalTokens": total_tokens, "eventId": "e-1"},
    )


def write_jsonl(path: Path, entries: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


def write_summary(path: Path, model="grok-4.6") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "info": {"id": SID, "cwd": PROJECT_DIR},
        "current_model_id": model,
        "num_messages": 10,
    }))
    return path


def write_prompt_history(path: Path, entries: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


@pytest.fixture
def sessions_dir(tmp_path):
    """A fixture session dir with two non-monotonic turn_completed batches."""
    root = tmp_path / "sessions"
    proj = root / encode_cwd(PROJECT_DIR)
    sdir = proj / SID
    write_jsonl(sdir / "updates.jsonl", [
        context_update(50_000, timestamp=1787072200),
        turn_completed(
            input_tokens=4_130_868, output_tokens=94_765, cached_read=3_339_776,
            reasoning=42_243, cost_ticks=7_295_125_400, num_turns=28,
            timestamp=1787072210, prompt_id="prompt-1",
        ),
        context_update(116_677, timestamp=1787072220),
        turn_completed(
            input_tokens=89_480, output_tokens=303, cached_read=128,
            reasoning=201, cost_ticks=306_996_200, num_turns=1,
            timestamp=1787072230, prompt_id="prompt-2",
        ),
    ])
    write_summary(sdir / "summary.json")
    write_prompt_history(proj / "prompt_history.jsonl", [
        {"timestamp": "2026-08-18T16:56:54Z", "session_id": SID, "prompt": "one", "is_bash": False},
        {"timestamp": "2026-08-18T17:00:00Z", "session_id": SID, "prompt": "two", "is_bash": False},
        {"timestamp": "2026-08-18T17:05:00Z", "session_id": OTHER_SID, "prompt": "not ours", "is_bash": False},
    ])
    return root


@pytest.fixture
def reader(sessions_dir):
    return GrokStatsReader(sessions_dir=sessions_dir)


def make_session(**over):
    base = dict(
        id="agent-1",
        name="grok-agent",
        tmux_session="agents",
        agent_session_ids=[SID],
        active_agent_session_id=SID,
        start_directory=PROJECT_DIR,
        start_time=LAUNCH.isoformat(),
        wrapper=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestEncoding:
    def test_leading_slash_and_dots(self):
        # Appendix B: full absolute path percent-encoded, "/" -> "%2F",
        # including the leading slash; dots stay literal.
        assert encode_cwd("/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok") == (
            "%2FUsers%2Fmike%2F.claude%2Fjobs%2Ff6bc7dbe%2Ftmp%2Fprobe-grok"
        )

    def test_matches_pythons_own_quote(self):
        assert encode_cwd(PROJECT_DIR) == quote(PROJECT_DIR, safe="")


class TestGetStats:
    def test_sums_across_non_monotonic_turn_batches(self, reader):
        # This is the load-bearing assertion for "per-turn, not cumulative":
        # a "latest wins" implementation would read only the second turn's
        # much smaller numbers.
        stats = reader.get_stats(make_session())
        assert stats.input_tokens == 4_130_868 + 89_480
        assert stats.output_tokens == (94_765 + 42_243) + (303 + 201)
        assert stats.cache_read_tokens == 3_339_776 + 128

    def test_current_context_is_the_latest_meta_total(self, reader):
        # 116_677 was written after 50_000 in file order.
        assert reader.get_stats(make_session()).current_context_tokens == 116_677

    def test_model_from_summary_json(self, reader):
        assert reader.get_stats(make_session()).model == "grok-4.6"

    def test_provider_is_left_alone(self, reader):
        assert reader.get_stats(make_session()).provider is None

    def test_interaction_count_matches_this_sessions_prompt_history_lines(self, reader):
        # Two lines tagged SID, one tagged a different session — must not count.
        assert reader.get_stats(make_session()).interaction_count == 2

    def test_no_matching_session_dir_is_unknown_not_zero(self, reader):
        stats = reader.get_stats(make_session(
            agent_session_ids=[], active_agent_session_id="not-a-real-sid",
        ))
        assert stats is None

    def test_wrong_cwd_is_unknown(self, reader):
        stats = reader.get_stats(make_session(start_directory="/somewhere/else"))
        assert stats is None


class TestStoredCost:
    def test_cost_ticks_are_nano_dollars_summed_across_turns(self, reader):
        # (7_295_125_400 + 306_996_200) / 1e9
        cost = reader.get_stored_cost(make_session())
        assert cost == pytest.approx(7.6021216, rel=1e-6)

    def test_zero_cost_is_none_not_zero(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        write_jsonl(sdir / "updates.jsonl", [
            turn_completed(input_tokens=10, output_tokens=1, cost_ticks=0),
        ])
        write_summary(sdir / "summary.json")
        reader = GrokStatsReader(sessions_dir=root)
        assert reader.get_stored_cost(make_session()) is None

    def test_missing_session_is_none(self, tmp_path):
        reader = GrokStatsReader(sessions_dir=tmp_path / "absent")
        assert reader.get_stored_cost(make_session()) is None


class TestSessionIdResolution:
    def test_prescribed_id_is_used_directly_no_discovery(self, reader):
        # SESSION_ID_PRESCRIPTION means overcode always already knows the id.
        assert reader.get_current_session_id(make_session(), LAUNCH) == SID

    def test_discover_session_ids_reports_the_owned_id_only(self, reader):
        found = reader.discover_session_ids(make_session(), LAUNCH, [])
        assert found.ids == []
        assert found.latest == SID

    def test_no_owned_id_resolves_to_nothing(self, reader):
        session = make_session(agent_session_ids=[], active_agent_session_id=None)
        assert reader.get_current_session_id(session, LAUNCH) is None
        assert reader.get_stats(session) is None


class TestWindowUsage:
    def test_window_since_before_launch_includes_both_turns(self, reader):
        usage = reader.get_window_token_usage(make_session(), datetime.fromtimestamp(0))
        assert usage["input_tokens"] == 4_130_868 + 89_480

    def test_window_after_first_turn_excludes_it(self, reader):
        usage = reader.get_window_token_usage(
            make_session(), datetime.fromtimestamp(1787072225)
        )
        assert usage["input_tokens"] == 89_480

    def test_missing_session_is_zeroed_not_raised(self, tmp_path):
        reader = GrokStatsReader(sessions_dir=tmp_path / "absent")
        usage = reader.get_window_token_usage(make_session(), LAUNCH)
        assert usage["input_tokens"] == 0


class TestSchemaDrift:
    def test_missing_usage_keys_surface_a_doctor_finding(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        drifted = update_envelope({
            "sessionUpdate": "turn_completed",
            "usage": {"inputTokens": 10},  # missing most keys
        })
        write_jsonl(sdir / "updates.jsonl", [drifted])
        monkeypatch.setenv("GROK_HOME", str(tmp_path))
        findings = schema_findings()
        assert len(findings) == 1
        assert "costUsdTicks" in findings[0]

    def test_intact_schema_reports_nothing(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        write_jsonl(sdir / "updates.jsonl", [turn_completed(100, 10, cost_ticks=1000)])
        monkeypatch.setenv("GROK_HOME", str(tmp_path))
        assert schema_findings() == []

    def test_no_sessions_is_not_a_finding(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "empty-grok-home"))
        assert schema_findings() == []

    def test_drift_degrades_get_stats_to_none_not_wrong_numbers(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        drifted = update_envelope({"sessionUpdate": "turn_completed", "usage": {}})
        write_jsonl(sdir / "updates.jsonl", [drifted])
        reader = GrokStatsReader(sessions_dir=root)
        assert reader.get_stats(make_session()) is None


class TestFailureIsolation:
    """Nothing the reader does may raise into a daemon tick."""

    def test_missing_sessions_directory(self, tmp_path):
        reader = GrokStatsReader(sessions_dir=tmp_path / "absent")
        session = make_session()
        assert reader.get_stats(session) is None
        # Prescription means the id is known independent of whether the
        # on-disk directory exists yet — only get_stats needs the directory.
        assert reader.get_current_session_id(session, LAUNCH) == SID
        assert reader.discover_session_ids(session, LAUNCH, []).ids == []
        assert reader.get_container_stats(session) is None

    def test_corrupt_updates_line_is_skipped(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        path = sdir / "updates.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write("not json at all\n")
            handle.write(json.dumps(turn_completed(100, 10, cost_ticks=1000)) + "\n")
        write_summary(sdir / "summary.json")
        reader = GrokStatsReader(sessions_dir=root)
        stats = reader.get_stats(make_session())
        assert stats is not None and stats.input_tokens == 100

    def test_session_without_a_directory(self, reader):
        session = make_session(start_directory=None)
        assert reader.get_stats(session) is None
        # The id itself is a property of the Session object (prescribed at
        # launch), not something that requires locating a directory on disk.
        assert reader.get_current_session_id(session, LAUNCH) == SID

    def test_missing_summary_json_still_reads_tokens(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        write_jsonl(sdir / "updates.jsonl", [turn_completed(100, 10, cost_ticks=1000)])
        reader = GrokStatsReader(sessions_dir=root)
        stats = reader.get_stats(make_session())
        assert stats is not None
        assert stats.model is None

    def test_missing_prompt_history_reads_zero_interactions(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        write_jsonl(sdir / "updates.jsonl", [turn_completed(100, 10, cost_ticks=1000)])
        write_summary(sdir / "summary.json")
        reader = GrokStatsReader(sessions_dir=root)
        assert reader.get_stats(make_session()).interaction_count == 0

    def test_empty_updates_file_is_unknown(self, tmp_path):
        root = tmp_path / "sessions"
        sdir = root / encode_cwd(PROJECT_DIR) / SID
        path = sdir / "updates.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("")
        reader = GrokStatsReader(sessions_dir=root)
        assert reader.get_stats(make_session()) is None

    def test_container_stats_are_not_available(self, reader):
        assert reader.get_container_stats(make_session(wrapper="devcontainer")) is None


class TestBackendWiring:
    def test_backend_hands_out_the_grok_reader(self):
        from overcode.backends.grok import get_grok_backend

        assert isinstance(get_grok_backend().make_stats_reader(), GrokStatsReader)

    def test_session_resolution_picks_it_up(self):
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session

        clear_reader_cache()
        try:
            reader = stats_reader_for_session(SimpleNamespace(backend="grok"))
            assert isinstance(reader, GrokStatsReader)
        finally:
            clear_reader_cache()
