"""CodexStatsReader against fixture rollout JSONL files built from the real shapes.

The event/payload shapes below (``session_meta``, ``event_msg`` ->
``token_count``, ``turn_context``, ``response_item`` -> ``message``) are
verbatim from Appendix A / §2.4 of ``docs/design/agent-backends-codex-grok.md``
(real Codex CLI v0.150.1 session artifacts, Phase 0 live verification), so
the fixture drifts only when codex's rollout schema does — at which point
``TestSchemaDrift`` documents what the reader does about it.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from overcode.backends.codex_stats import (
    CodexStatsReader,
    codex_home,
    schema_findings,
    session_ids_from_hook_state,
    sessions_root,
)

LAUNCH = datetime(2026, 8, 20, 12, 0, 0)
PROJECT_DIR = "/tmp/codex-project"
SID = "01a0439d-63b8-71d0-bf11-38fb10d0f551"
OTHER_SID = "01a0439d-63b8-71d0-bf11-38fb10d0f552"


def token_count_event(
    input_tokens=7000, cached=512, cache_write=64, output=100, reasoning=20,
    total=7120, context_window=272000,
):
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": cache_write,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": total,
                },
                "last_token_usage": {},
                "model_context_window": context_window,
            },
            "rate_limits": {},
        },
    }


def turn_context_event(model="gpt-5.6-sol"):
    return {
        "type": "turn_context",
        "payload": {
            "model": model,
            "collaboration_mode": {"settings": {"model": model}},
        },
    }


def user_turn_item(text="count from 1 to 20"):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "id": "msg_1",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["user.text"],
            },
        },
    }


def scaffolding_item(kind="environments.environment_context"):
    """A real user turn shape, but scaffolding — must NOT count as an interaction."""
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "id": "msg_scaffold",
            "role": "user",
            "content": [{"type": "input_text", "text": "<environment_context>...</environment_context>"}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": [kind],
            },
        },
    }


def session_meta_line(session_id=SID, cwd=PROJECT_DIR, cli_version="0.150.1"):
    return {
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": cwd, "cli_version": cli_version},
    }


def write_rollout(path: Path, entries: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


def day_dir(root: Path, dt: datetime = LAUNCH) -> Path:
    return root / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}"


def rollout_path(root: Path, session_id: str, ts: str = "2026-08-20T12-00-00", dt: datetime = LAUNCH) -> Path:
    return day_dir(root, dt) / f"rollout-{ts}-{session_id}.jsonl"


@pytest.fixture
def sessions_dir(tmp_path):
    """A fixture rollout tree with one normal multi-turn session."""
    root = tmp_path / "sessions"
    write_rollout(
        rollout_path(root, SID),
        [
            session_meta_line(),
            turn_context_event(),
            user_turn_item(),
            token_count_event(),
        ],
    )
    return root


@pytest.fixture
def reader(sessions_dir):
    return CodexStatsReader(sessions_dir=sessions_dir)


def make_session(**over):
    base = dict(
        id="agent-1",
        name="cx-agent",
        tmux_session="agents",
        agent_session_ids=[SID],
        active_agent_session_id=SID,
        start_directory=PROJECT_DIR,
        start_time=LAUNCH.isoformat(),
        wrapper=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestLocation:
    def test_default_codex_home(self, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        assert codex_home() == Path.home() / ".codex"

    def test_codex_home_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert codex_home() == tmp_path
        assert sessions_root() == tmp_path / "sessions"


class TestGetStats:
    def test_reads_tokens_from_the_latest_token_count_event(self, reader):
        stats = reader.get_stats(make_session())
        assert stats.input_tokens == 7000
        # reasoning has no bucket of its own, so it folds into output rather
        # than vanishing from the totals (matches the opencode convention).
        assert stats.output_tokens == 120
        assert stats.cache_read_tokens == 512
        assert stats.cache_creation_tokens == 64
        assert stats.current_context_tokens == 7120

    def test_model_from_turn_context(self, reader):
        assert reader.get_stats(make_session()).model == "gpt-5.6-sol"

    def test_provider_is_left_alone(self, reader):
        # `provider` is overcode's API-transport discriminator, not the
        # model vendor — same posture as OpencodeStatsReader.
        assert reader.get_stats(make_session()).provider is None

    def test_interaction_count_excludes_scaffolding(self, sessions_dir):
        write_rollout(
            rollout_path(sessions_dir, SID),
            [
                session_meta_line(),
                turn_context_event(),
                scaffolding_item("environments.environment_context"),
                scaffolding_item("host_skills.instructions"),
                user_turn_item(),
                user_turn_item("a second real turn"),
                token_count_event(),
            ],
        )
        reader = CodexStatsReader(sessions_dir=sessions_dir)
        assert reader.get_stats(make_session()).interaction_count == 2

    def test_latest_token_count_event_wins(self, sessions_dir):
        write_rollout(
            rollout_path(sessions_dir, SID),
            [
                session_meta_line(),
                turn_context_event(),
                user_turn_item(),
                token_count_event(input_tokens=100, total=200),
                token_count_event(input_tokens=9000, total=9500),  # cumulative, latest wins
            ],
        )
        reader = CodexStatsReader(sessions_dir=sessions_dir)
        stats = reader.get_stats(make_session())
        assert stats.input_tokens == 9000
        assert stats.current_context_tokens == 9500

    def test_reported_context_window_is_surfaced(self, reader):
        """#469: codex's own rollout JSONL reports the real context window
        per token_count event (`payload.info.model_context_window`) — the
        fixture's default is 272000 (see token_count_event's default arg).
        """
        stats = reader.get_stats(make_session())
        assert stats.reported_context_window == 272000

    def test_reported_context_window_preferred_over_static_table(self, sessions_dir):
        """max_context_tokens must use codex's own reported figure even
        when it disagrees with overcode's static gpt-5.6-sol table entry
        (258400) — the CLI's live number is more authoritative than a
        table snapshot."""
        write_rollout(
            rollout_path(sessions_dir, SID),
            [
                session_meta_line(),
                turn_context_event(model="gpt-5.6-sol"),
                user_turn_item(),
                token_count_event(context_window=999999),
            ],
        )
        reader = CodexStatsReader(sessions_dir=sessions_dir)
        stats = reader.get_stats(make_session())
        assert stats.reported_context_window == 999999
        assert stats.max_context_tokens == 999999

    def test_latest_reported_context_window_wins(self, sessions_dir):
        """A running-total field like the usage counters — later events
        overwrite earlier ones, same convention as token_count itself."""
        write_rollout(
            rollout_path(sessions_dir, SID),
            [
                session_meta_line(),
                turn_context_event(),
                user_turn_item(),
                token_count_event(context_window=200000),
                token_count_event(context_window=258400),
            ],
        )
        reader = CodexStatsReader(sessions_dir=sessions_dir)
        assert reader.get_stats(make_session()).reported_context_window == 258400

    def test_missing_reported_window_falls_back_to_static_table(self, sessions_dir):
        """A rollout event with no model_context_window field at all (older
        codex CLI, or schema drift) must not crash — max_context_tokens
        falls back to the static table for a recognized model."""
        entry = token_count_event()
        del entry["payload"]["info"]["model_context_window"]
        write_rollout(
            rollout_path(sessions_dir, SID),
            [session_meta_line(), turn_context_event(model="gpt-5.6-sol"), user_turn_item(), entry],
        )
        reader = CodexStatsReader(sessions_dir=sessions_dir)
        stats = reader.get_stats(make_session())
        assert stats.reported_context_window is None
        assert stats.max_context_tokens == 258_400

    def test_unknown_session_falls_back_to_cwd_match(self, reader):
        stats = reader.get_stats(
            make_session(agent_session_ids=[], active_agent_session_id=None)
        )
        assert stats is not None and stats.input_tokens == 7000

    def test_no_matching_file_is_unknown_not_zero(self, reader):
        stats = reader.get_stats(
            make_session(
                start_directory="/somewhere/else",
                agent_session_ids=[],
                active_agent_session_id=None,
            )
        )
        assert stats is None

    def test_two_sessions_same_cwd_disambiguation(self, tmp_path):
        # Two conversations in the same project directory: the hook-recorded
        # id must win over the more-recent-by-filename fallback.
        root = tmp_path / "sessions"
        write_rollout(
            rollout_path(root, SID, ts="2026-08-20T10-00-00"),
            [session_meta_line(session_id=SID), turn_context_event("gpt-5.6-sol"),
             user_turn_item(), token_count_event(input_tokens=1000, total=1100)],
        )
        write_rollout(
            rollout_path(root, OTHER_SID, ts="2026-08-20T11-00-00"),
            [session_meta_line(session_id=OTHER_SID), turn_context_event("gpt-5.6-mini"),
             user_turn_item(), token_count_event(input_tokens=2000, total=2100)],
        )
        reader = CodexStatsReader(sessions_dir=root)

        # Hook state names the *earlier* file explicitly — must not drift to
        # the textually-later one just because it sorts last.
        stats = reader.get_stats(
            make_session(agent_session_ids=[SID], active_agent_session_id=SID)
        )
        assert stats.input_tokens == 1000
        assert stats.model == "gpt-5.6-sol"

        # No hook-recorded id at all: cwd fallback picks the most recent by
        # filename (both are equally "this agent's cwd", so recency is the
        # only signal available).
        stats_fallback = reader.get_stats(
            make_session(agent_session_ids=[], active_agent_session_id=None)
        )
        assert stats_fallback.input_tokens == 2000


class TestStoredCost:
    def test_no_local_cost_figure(self, reader):
        # codex is subscription/API billed; nothing is stored locally, so
        # the caller always falls back to pricing.py.
        assert reader.get_stored_cost(make_session()) is None


class TestSessionIdDiscovery:
    def test_hook_recorded_ids_win(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        state = tmp_path / "state" / "agents"
        state.mkdir(parents=True)
        (state / "hook_state_cx-agent.json").write_text(
            json.dumps({
                "event": "SessionStart",
                "timestamp": 0,
                "agent_session_ids": [OTHER_SID, SID],
                "agent_session_id": SID,
            })
        )
        assert session_ids_from_hook_state(make_session()) == [OTHER_SID, SID]
        assert reader.get_current_session_id(make_session(), LAUNCH) == SID

    def test_cwd_fallback_when_no_hook_state(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        assert reader.get_current_session_id(make_session(), LAUNCH) == SID

    def test_discovers_unowned_ids(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        found = reader.discover_session_ids(
            make_session(agent_session_ids=[], active_agent_session_id=None), LAUNCH, []
        )
        assert found.ids == [SID]
        assert found.latest == SID

    def test_never_steals_another_agent_s_session(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        other_agent = make_session(id="agent-2", name="other")
        found = reader.discover_session_ids(
            make_session(agent_session_ids=[], active_agent_session_id=None),
            LAUNCH,
            [other_agent],
        )
        assert found.ids == [] and found.latest is None

    def test_corrupt_hook_state_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        state = tmp_path / "state" / "agents"
        state.mkdir(parents=True)
        (state / "hook_state_cx-agent.json").write_text("{not json")
        assert session_ids_from_hook_state(make_session()) == []


class TestWindowUsage:
    def test_full_session_when_launch_is_within_window(self, reader):
        usage = reader.get_window_token_usage(make_session(), LAUNCH - timedelta(hours=1))
        assert usage["input_tokens"] == 7000
        assert usage["output_tokens"] == 120
        assert usage["cache_read_tokens"] == 512

    def test_unknown_when_window_starts_after_launch(self, reader):
        # token_count events are cumulative totals with no reliable
        # per-event timestamp to diff against, so a window that only covers
        # part of the session degrades to unknown rather than a wrong slice.
        usage = reader.get_window_token_usage(make_session(), LAUNCH + timedelta(hours=1))
        assert usage == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }

    def test_missing_session_is_zeroed_not_raised(self, tmp_path):
        reader = CodexStatsReader(sessions_dir=tmp_path / "absent")
        session = make_session(agent_session_ids=[], active_agent_session_id=None)
        assert reader.get_window_token_usage(session, LAUNCH)["input_tokens"] == 0


class TestSchemaDrift:
    def test_missing_usage_keys_surface_a_doctor_finding(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        drifted_event = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 100},  # missing most keys
                    "model_context_window": 200000,
                },
            },
        }
        write_rollout(
            rollout_path(root, SID, dt=datetime.now()),
            [session_meta_line(), drifted_event],
        )
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        # sessions_root() reads CODEX_HOME/sessions, so point CODEX_HOME at
        # tmp_path directly (root already equals tmp_path/"sessions").
        findings = schema_findings()
        assert len(findings) == 1
        assert "output_tokens" in findings[0]

    def test_intact_schema_reports_nothing(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        write_rollout(
            rollout_path(root, SID, dt=datetime.now()),
            [session_meta_line(), turn_context_event(), user_turn_item(), token_count_event()],
        )
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert schema_findings() == []

    def test_no_sessions_is_not_a_finding(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
        assert schema_findings() == []

    def test_drift_degrades_get_stats_to_none_not_wrong_numbers(self, tmp_path):
        root = tmp_path / "sessions"
        drifted_event = {
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": {}}},
        }
        write_rollout(rollout_path(root, SID), [session_meta_line(), drifted_event])
        reader = CodexStatsReader(sessions_dir=root)
        # No usable model/tokens/interactions were actually read — reads as
        # unknown, not an all-zero session.
        assert reader.get_stats(make_session()) is None


class TestFailureIsolation:
    """Nothing the reader does may raise into a daemon tick."""

    def test_missing_sessions_directory(self, tmp_path):
        reader = CodexStatsReader(sessions_dir=tmp_path / "absent")
        session = make_session()
        assert reader.get_stats(session) is None
        assert reader.get_current_session_id(session, LAUNCH) is None
        assert reader.discover_session_ids(session, LAUNCH, []).ids == []
        assert reader.get_container_stats(session) is None

    def test_corrupt_rollout_line_is_skipped(self, tmp_path):
        root = tmp_path / "sessions"
        path = rollout_path(root, SID)
        path.parent.mkdir(parents=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(session_meta_line()) + "\n")
            handle.write("not json at all\n")
            handle.write(json.dumps(turn_context_event()) + "\n")
            handle.write(json.dumps(user_turn_item()) + "\n")
            handle.write(json.dumps(token_count_event()) + "\n")
        reader = CodexStatsReader(sessions_dir=root)
        stats = reader.get_stats(make_session())
        assert stats is not None and stats.interaction_count == 1

    def test_session_without_a_directory(self, reader):
        session = make_session(
            start_directory=None, agent_session_ids=[], active_agent_session_id=None
        )
        assert reader.get_stats(session) is None
        assert reader.get_current_session_id(session, LAUNCH) is None

    def test_unparseable_start_time(self, reader):
        session = make_session(
            start_time="not-a-timestamp",
            agent_session_ids=[],
            active_agent_session_id=None,
        )
        assert reader.get_stats(session) is None

    def test_empty_rollout_file_is_unknown(self, tmp_path):
        root = tmp_path / "sessions"
        path = rollout_path(root, SID)
        path.parent.mkdir(parents=True)
        path.write_text("")
        reader = CodexStatsReader(sessions_dir=root)
        assert reader.get_stats(make_session()) is None

    def test_container_stats_are_not_available(self, reader):
        assert reader.get_container_stats(make_session(wrapper="devcontainer")) is None


class TestBackendWiring:
    def test_backend_hands_out_the_rollout_reader(self):
        from overcode.backends.codex import get_codex_backend

        assert isinstance(get_codex_backend().make_stats_reader(), CodexStatsReader)

    def test_session_resolution_picks_it_up(self):
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session

        clear_reader_cache()
        try:
            reader = stats_reader_for_session(SimpleNamespace(backend="codex"))
            assert isinstance(reader, CodexStatsReader)
        finally:
            clear_reader_cache()
