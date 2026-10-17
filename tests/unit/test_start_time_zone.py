"""An agent's Claude spend counts from its start time in every time zone.

Transcripts store UTC; the readers key each message by its naive local
time, so the start filter must be naive local too. A UTC-naive filter
dropped an agent's first hours west of UTC and let in messages from
before its start east of it.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from overcode.history_reader import encode_project_path, get_session_stats
from overcode.session_manager import Session

SID = "0f0f0f0f-0000-4000-8000-0000000017e5"
PROJECT = "/work/repo"


@pytest.fixture(params=["America/Los_Angeles", "Europe/London", "Asia/Tokyo", "UTC"])
def zone(request, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _reply(n: int, at: datetime, output: int) -> dict:
    return {"type": "assistant", "sessionId": SID, "uuid": f"r{n}", "timestamp": _utc(at),
            "message": {"id": f"msg_tz_{n}", "role": "assistant", "model": "claude-haiku-5-5",
                        "usage": {"input_tokens": 1, "output_tokens": output,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}


def test_counts_from_the_agent_s_start(zone, tmp_path):
    start = datetime.now().replace(microsecond=0) - timedelta(hours=2)  # naive local
    before = start - timedelta(minutes=30)  # a message from before this agent began
    after = start + timedelta(minutes=20)
    root = tmp_path / "projects"
    folder = root / encode_project_path(PROJECT)
    folder.mkdir(parents=True)
    (folder / f"{SID}.jsonl").write_text(
        json.dumps(_reply(1, before.astimezone(), 100)) + "\n"
        + json.dumps(_reply(2, after.astimezone(), 7)) + "\n")
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"display": "go", "project": PROJECT, "sessionId": SID,
                                   "timestamp": int(after.timestamp() * 1000)}) + "\n")
    session = Session(id="agent-1", name="agent-1", tmux_session="agents", tmux_window="1",
                      command=["claude"], start_directory=PROJECT, start_time=start.isoformat())
    session.agent_session_ids = [SID]

    stats = get_session_stats(session, history_path=history, projects_path=root)

    assert stats.output_tokens == 7, zone
