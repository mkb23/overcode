"""Shared, mtime-gated readers on the TUI 5 s sweep and the daemon's discovery (audit R8).

The TUI app holds one HistoryFile (a fresh one per sweep re-parsed
history.jsonl every 5 s), one SessionManager (the launcher used to build a
second one), and every ``Path.resolve()`` of a project string is memoised.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from overcode import history_reader
from overcode.history_reader import HistoryFile, resolve_project_path


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


class TestResolveMemo:
    def test_matches_path_resolve_and_is_memoised(self, tmp_path, monkeypatch):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        expected = str(real.resolve())
        assert resolve_project_path(str(link)) == str(Path(link).resolve()) == expected
        calls = []
        original = Path.resolve

        def counting(self, *a, **k):
            calls.append(str(self))
            return original(self, *a, **k)

        monkeypatch.setattr(Path, "resolve", counting)
        for _ in range(3):
            assert resolve_project_path(str(link)) == expected
        assert calls == []  # memo hits
        missing = str(tmp_path / "missing")
        assert resolve_project_path(missing) == resolve_project_path(missing)
        assert calls == [missing]

    def test_encode_project_path_uses_the_memo(self, tmp_path):
        d = tmp_path / "proj_dir"
        d.mkdir()
        encoded = history_reader.encode_project_path(str(d))
        assert encoded == history_reader._NON_ALNUM.sub("-", str(d.resolve()))
        assert str(d) in history_reader._resolved_paths

    def test_clear_transcript_caches_drops_the_memo(self, tmp_path):
        resolve_project_path(str(tmp_path))
        assert history_reader._resolved_paths
        history_reader.clear_transcript_caches()
        assert not history_reader._resolved_paths


class TestHistoryFileSharedAccess:
    def _history(self, tmp_path, n=3):
        path = tmp_path / "history.jsonl"
        entries = [
            {
                "display": f"p{i}",
                "timestamp": 1_700_000_000_000 + i,
                "project": str(tmp_path),
                "sessionId": f"sid-{i}",
            }
            for i in range(n)
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(path, (1_700_000_000, 1_700_000_000))
        return path

    def test_signature_tracks_the_file(self, tmp_path):
        path = self._history(tmp_path)
        hf = HistoryFile(path)
        sig = hf.signature()
        assert sig is not None and sig.size == path.stat().st_size
        assert hf.signature() == sig
        with open(path, "a") as f:
            f.write(
                json.dumps({"display": "x", "timestamp": 1, "project": "/p", "sessionId": "s"})
                + "\n"
            )
        assert hf.signature() != sig
        assert HistoryFile(tmp_path / "missing.jsonl").signature() is None

    def test_iter_entries_is_the_cached_list_and_read_all_a_copy(self, tmp_path):
        hf = HistoryFile(self._history(tmp_path))
        entries = hf.iter_entries()
        assert len(entries) == 3
        assert hf.iter_entries() is entries  # unchanged file: no re-parse, no copy
        copy = hf.read_all()
        assert copy == entries and copy is not entries

    def test_directory_fallback_resolves_each_project_once(self, tmp_path, monkeypatch):
        path = tmp_path / "history.jsonl"
        project = tmp_path / "proj"
        project.mkdir()
        entries = [
            {
                "display": f"p{i}",
                "timestamp": 1_800_000_000_000 + i,
                "project": str(project),
                "sessionId": f"sid-{i}",
            }
            for i in range(50)
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        hf = HistoryFile(path)

        class S:
            start_directory = str(project)
            start_time = datetime.fromtimestamp(1_700_000_000).isoformat()
            agent_session_ids = []  # no owned ids: the directory-matching fallback

        calls = []
        original = Path.resolve

        def counting(self, *a, **k):
            calls.append(str(self))
            return original(self, *a, **k)

        monkeypatch.setattr(Path, "resolve", counting)
        assert len(hf.get_interactions_for_session(S())) == 50
        assert len(calls) == 1  # one distinct project string, resolved once
        assert len(hf.get_interactions_for_session(S())) == 50
        assert len(calls) == 1


class TestAppSharedState:
    def test_app_holds_one_session_manager_and_one_history_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / ".overcode"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / ".overcode" / "state"))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        from overcode import settings

        monkeypatch.setattr(settings.PATHS, "base_dir", tmp_path / ".overcode")
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI("test-session", diagnostics=True)
        assert app.launcher.sessions is app.session_manager
        assert isinstance(app._history_file, HistoryFile)
        assert app._history_file is app._history_file


class TestHistoryIndexes:
    """get_interactions_for_session answers from per-parse indexes, in file order."""

    def _history(self, tmp_path, entries):
        path = tmp_path / "history.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return path

    def _scan(self, entries, session):
        """The pre-index scan, as a reference."""
        start_ms = int(datetime.fromisoformat(session.start_time).timestamp() * 1000)
        owned = set(session.agent_session_ids or [])
        session_dir = str(Path(session.start_directory).resolve())
        out = []
        for e in entries:
            if e.timestamp_ms < start_ms:
                continue
            if owned:
                if e.session_id in owned:
                    out.append(e)
            elif e.project and str(Path(e.project).resolve()) == session_dir:
                out.append(e)
        return out

    def test_owned_ids_and_directory_fallback_match_a_scan(self, tmp_path):
        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        proj_a.mkdir()
        proj_b.mkdir()
        link_a = tmp_path / "link-a"
        link_a.symlink_to(proj_a)
        base = 1_800_000_000_000
        raw = []
        sids = ["s1", "s2", "s3", None]
        projects = [str(proj_a), str(link_a), str(proj_b), None]
        for i in range(120):
            raw.append(
                {
                    "display": f"p{i}",
                    "timestamp": base + i * 1000,
                    "project": projects[i % 4],
                    "sessionId": sids[(i * 7) % 4],
                }
            )
        raw.append({"display": "no-ts", "project": str(proj_a), "sessionId": "s1"})
        hf = HistoryFile(self._history(tmp_path, raw))
        entries = hf.read_all()
        assert len(entries) == 121

        class S:
            def __init__(self, directory, ids, start_ms):
                self.start_directory = directory
                self.agent_session_ids = ids
                self.start_time = datetime.fromtimestamp(start_ms / 1000).isoformat()

        cases = [
            S(str(proj_a), ["s1"], base),
            S(str(proj_a), ["s1", "s3"], base + 30_000),
            S(str(proj_b), ["s2", "missing"], base),
            S(str(proj_a), [], base),  # fallback: proj_a and link-a both resolve to a
            S(str(link_a), [], base + 60_000),
            S(str(proj_b), [], base),
            S(str(tmp_path / "none"), [], base),
        ]
        for session in cases:
            got = hf.get_interactions_for_session(session)
            assert got == self._scan(entries, session)
            assert [id(e) for e in got] == [id(e) for e in self._scan(entries, session)]
        assert hf.get_session_ids_for_session(cases[3]) == ["s1"]
        assert hf.count_interactions(cases[0]) == len(self._scan(entries, cases[0]))

    def test_indexes_follow_the_file(self, tmp_path):
        base = 1_800_000_000_000
        path = self._history(
            tmp_path,
            [{"display": "a", "timestamp": base, "project": str(tmp_path), "sessionId": "s1"}],
        )
        os.utime(path, (1_700_000_000, 1_700_000_000))
        hf = HistoryFile(path)

        class S:
            start_directory = str(tmp_path)
            agent_session_ids = ["s1"]
            start_time = datetime.fromtimestamp(1_700_000_000).isoformat()

        assert [e.display for e in hf.get_interactions_for_session(S())] == ["a"]
        with open(path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "display": "b",
                        "timestamp": base + 1,
                        "project": str(tmp_path),
                        "sessionId": "s1",
                    }
                )
                + "\n"
            )
        os.utime(path, (1_700_000_001, 1_700_000_001))
        assert [e.display for e in hf.get_interactions_for_session(S())] == ["a", "b"]
        path.unlink()
        assert hf.get_interactions_for_session(S()) == []
