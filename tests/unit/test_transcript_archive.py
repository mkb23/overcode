"""
The transcript archive (transcript_archive, transcript_queue, cli/archive):
sweep versioning, permissions, no content outside the copies, the snapshot
import, the hook queue, and find/cat (shirka's interface).

Every transcript here is synthetic; key-like strings are assembled at run
time so none is written in the repository.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode import transcript_archive as ta
from overcode import transcript_queue
from overcode.transcript_archive import (
    ArchiveScheduler, Identities, Result, TranscriptArchive, classify, normalize_remote,
)

pytestmark = pytest.mark.skipif(ta.zstd_binary() is None, reason="zstd not installed")

SID = "11111111-2222-4333-8444-555555555555"
SID2 = "66666666-7777-4888-9999-aaaaaaaaaaaa"
MARKER = "SYNTHETIC-CONTENT-MARKER-7f3a"
OLD = time.time() - 3 * 3600


def _record(i, cwd, ts="2026-10-20T10:00:00.000Z", text=MARKER, branch="main"):
    return json.dumps({
        "type": "user", "uuid": f"u{i}", "sessionId": SID, "timestamp": ts,
        "cwd": cwd, "version": "2.1.300", "gitBranch": branch,
        "message": {"role": "user", "content": f"{text} line {i}"},
    }) + "\n"


def _git_repo(path: Path, remote: str) -> Path:
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("remote", "add", "origin", remote)
    run("-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-q", "--allow-empty", "-m", "root")
    return path


def _age(path: Path, when: float = OLD) -> None:
    os.utime(path, (when, when))


@pytest.fixture
def env(tmp_path):
    """A Claude directory with one conversation, an archive root, a repo."""
    repo = _git_repo(tmp_path / "code" / "shirka", "git@github.com:Example-Org/shirka.git")
    claude = tmp_path / "claude"
    project = claude / "projects" / "-tmp-code-shirka"
    (project / SID / "subagents").mkdir(parents=True)
    (project / SID / "tool-results").mkdir()
    main = project / f"{SID}.jsonl"
    main.write_text("".join(_record(i, str(repo)) for i in range(3)))
    sub = project / SID / "subagents" / "agent-a1b2.jsonl"
    sub.write_text(_record(9, str(repo), ts="2026-10-20T10:05:00.000Z"))
    (project / SID / "subagents" / "agent-a1b2.meta.json").write_text('{"agentType":"x"}')
    (project / SID / "tool-results" / "toolu_01.txt").write_text(f"{MARKER} tool output")
    for p in (main, sub):
        _age(p)
    identities = Identities.from_records([{
        "id": "agent-id-1", "name": "shirka-ui", "billing_code": "shirka",
        "billing_code_id": "c_1", "parent_session_id": "agent-id-0",
        "agent_session_ids": [SID],
    }, {"id": "agent-id-0", "name": "shirka", "agent_session_ids": []}])
    archive = TranscriptArchive(tmp_path / "transcripts", source_root=claude,
                                identities=identities)
    return {"tmp": tmp_path, "claude": claude, "project": project, "main": main,
            "sub": sub, "repo": repo, "archive": archive}


def _sweep(archive, **kw) -> Result:
    with archive.locked():
        return archive.sweep(**kw)


def _content(archive, row) -> bytes:
    out = io.BytesIO()
    ta.decompress_to(archive.root / row["stored"], out)
    return out.getvalue()


def _rows(archive):
    return list(ta.read_manifest(archive.manifest_path))


# ── layout and classification ────────────────────────────────────────────

class TestClassify:
    def test_main_subagent_tool_result_and_set_aside(self):
        assert classify(f"projects/p/{SID}.jsonl").kind == ta.KIND_MAIN
        sub = classify(f"projects/p/{SID}/subagents/workflows/wf_1/agent-x.jsonl")
        assert (sub.kind, sub.inner) == (ta.KIND_SUBAGENT, "subagents/workflows/wf_1/agent-x.jsonl")
        assert classify(f"projects/p/{SID}/tool-results/t.txt").kind == ta.KIND_TOOL_RESULT
        assert classify(f"projects/p/{SID}/custom-title.json").kind == ta.KIND_SESSION_FILE
        for name in (f"{SID}.jsonl.superseded-2026-10-01T10:00:00.123Z",
                     f"{SID}.orphaned-1730000000.jsonl"):
            ref = classify(f"projects/p/{name}")
            assert (ref.kind, ref.key) == (ta.KIND_MAIN, f"{SID}/main.jsonl")

    def test_other_files_have_a_place(self):
        assert classify("projects/p/memory/MEMORY.md").kind == ta.KIND_PROJECT_FILE
        assert classify("history.jsonl").kind == ta.KIND_HISTORY
        fh = classify(f"file-history/{SID}/abc@v2")
        assert (fh.kind, fh.cc_session) == (ta.KIND_FILE_HISTORY, SID)
        assert classify("projects/p/._x.jsonl") is None
        assert classify("debug/log.txt") is None

    def test_stored_paths(self):
        ref = classify(f"projects/p/{SID}.jsonl")
        assert ta.stored_path(ref, 2, "2026-10") == f"2026-10/{SID}/main.v2.jsonl.zst"
        ref = classify(f"projects/p/{SID}/subagents/agent-x.meta.json")
        assert ta.stored_path(ref, 1, "2026-10") == f"2026-10/{SID}/subagents/agent-x.v1.meta.json.zst"
        ref = classify(f"file-history/{SID}/abc@v2")
        assert ta.stored_path(ref, 1, "2026-09") == f"2026-09/{SID}/file-history/abc@v2.v1.zst"


class TestNormalizeRemote:
    @pytest.mark.parametrize("url", [
        "git@github.com:Example-Org/shirka.git",
        "https://github.com/Example-Org/shirka",
        "https://user:tok@GitHub.com/Example-Org/shirka.git/",
        "ssh://git@github.com:22/Example-Org/shirka.git",
        "github.com/Example-Org/shirka",
    ])
    def test_spellings(self, url):
        assert normalize_remote(url) == "github.com/Example-Org/shirka"

    def test_nothing(self):
        assert normalize_remote(None) is None
        assert normalize_remote("  ") is None


# ── the sweep ────────────────────────────────────────────────────────────

class TestSweep:
    def test_captures_every_file_byte_exact(self, env):
        archive = env["archive"]
        result = _sweep(archive)
        assert result.captured == 4 and result.failed == 0
        rows = _rows(archive)
        main = next(r for r in rows if r["kind"] == "main")
        assert main["stored"] == f"2026-10/{SID}/main.v1.jsonl.zst"
        assert main["rel"] == "main.v1.jsonl.zst"
        assert _content(archive, main) == env["main"].read_bytes()
        assert main["source_path"].endswith(f"projects/-tmp-code-shirka/{SID}.jsonl")
        assert (main["first_ts"], main["last_ts"]) == ("2026-10-20T10:00:00.000Z",) * 2
        assert main["cc_version"] == "2.1.300"
        assert main["git_remote"] == "github.com/Example-Org/shirka"
        assert main["git_branch"] == "main"
        assert (main["agent"], main["agent_id"], main["billing_code"], main["parent_agent"]) == (
            "shirka-ui", "agent-id-1", "shirka", "shirka")
        assert main["v"] == 1 and main["backend"] == "claude-code"
        kinds = sorted(r["kind"] for r in rows)
        assert kinds == ["main", "subagent", "subagent", "tool-result"]
        meta = json.loads((archive.root / "2026-10" / SID / "meta.json").read_text())
        assert meta["kind"] == "main" and meta["sha256"] == main["sha256"]

    def test_twice_is_a_no_op(self, env):
        archive = env["archive"]
        _sweep(archive)
        before = archive.manifest_path.read_bytes()
        result = _sweep(archive)
        assert result.captured == 0 and result.unchanged == result.seen
        assert archive.manifest_path.read_bytes() == before
        # A touch without a change is hashed once, then not again
        os.utime(env["main"], (OLD + 5, OLD + 5))
        assert _sweep(archive).captured == 0
        assert archive.manifest_path.read_bytes() == before

    def test_append_replaces_the_version(self, env):
        archive = env["archive"]
        _sweep(archive)
        with open(env["main"], "a") as f:
            f.write(_record(3, str(env["repo"]), ts="2026-10-21T09:00:00.000Z"))
        _age(env["main"])
        result = _sweep(archive)
        assert (result.captured, result.appended) == (1, 1)
        mains = [r for r in _rows(archive) if r["kind"] == "main"]
        assert [r["version"] for r in mains] == [1, 1]
        assert mains[-1]["last_ts"] == "2026-10-21T09:00:00.000Z"
        assert _content(archive, mains[-1]) == env["main"].read_bytes()
        assert sorted(p.name for p in (archive.root / "2026-10" / SID).glob("main.*")) == [
            "main.v1.jsonl.zst"]

    def test_append_to_a_busy_file_waits_unless_now(self, env):
        archive = env["archive"]
        _sweep(archive)
        with open(env["main"], "a") as f:
            f.write(_record(3, str(env["repo"])))
        result = _sweep(archive)
        assert (result.captured, result.deferred) == (0, 1)
        assert _sweep(archive, now=True).appended == 1

    def test_rewrite_keeps_the_old_version(self, env):
        archive = env["archive"]
        _sweep(archive)
        old = env["main"].read_bytes()
        env["main"].write_text(_record(0, str(env["repo"]), text="rewritten"))
        _age(env["main"])
        result = _sweep(archive)
        assert (result.captured, result.versioned) == (1, 1)
        session = archive.root / "2026-10" / SID
        assert sorted(p.name for p in session.glob("main.*")) == [
            "main.v1.jsonl.zst", "main.v2.jsonl.zst"]
        rows = {r["version"]: r for r in _rows(archive) if r["kind"] == "main"}
        assert _content(archive, rows[1]) == old
        assert _content(archive, rows[2]) == env["main"].read_bytes()

    def test_set_aside_copies(self, env):
        archive = env["archive"]
        _sweep(archive)
        original = env["main"].read_bytes()
        # Claude sets the transcript aside and starts again
        aside = env["project"] / f"{SID}.jsonl.superseded-2026-10-21T10:00:00Z"
        aside.write_bytes(original)
        env["main"].write_text(_record(0, str(env["repo"]), text="fresh"))
        for p in (aside, env["main"]):
            _age(p)
        result = _sweep(archive)
        # The copy is content already kept; the live file is new content
        assert (result.captured, result.versioned) == (1, 1)
        # Growing the live file again appends to its own version only
        with open(env["main"], "a") as f:
            f.write(_record(1, str(env["repo"])))
        _age(env["main"])
        assert _sweep(archive).appended == 1
        rows = {r["version"]: r for r in archive.current_rows() if r["kind"] == "main"}
        assert _content(archive, rows[1]) == original
        assert _content(archive, rows[2]) == env["main"].read_bytes()
        # A different set-aside copy is a version of its own
        other = env["project"] / f"{SID}.orphaned-1730000000.jsonl"
        other.write_text(_record(5, str(env["repo"]), text="orphan"))
        _age(other)
        assert _sweep(archive).versioned == 1
        assert {r["version"] for r in archive.current_rows() if r["kind"] == "main"} == {1, 2, 3}
        # cat picks the live file's version
        out = io.BytesIO()
        archive.cat(SID, out)
        assert out.getvalue() == env["main"].read_bytes()

    def test_a_growing_file_is_read_as_a_snapshot(self, env):
        archive = env["archive"]
        ref = classify(f"projects/-tmp-code-shirka/{SID}.jsonl")
        st = os.stat(env["main"])
        snapshot = env["main"].read_bytes()
        with open(env["main"], "a") as f:  # the writer appends after the stat
            f.write('{"type":"assistant","timestamp":"2026-10-2')
        with archive.locked():
            assert archive.capture_file(str(env["main"]), ref, origin="sweep",
                                        result=Result(), st=st) == "captured"
        (row,) = [r for r in _rows(archive) if r["kind"] == "main"]
        assert _content(archive, row) == snapshot
        # The next sweep sees the grown file as an append (when quiet)
        with open(env["main"], "a") as f:
            f.write('2T00:00:00.000Z"}\n')
        _age(env["main"])
        assert _sweep(archive).appended == 1

    def test_identity_is_null_for_sessions_overcode_did_not_launch(self, env):
        archive = TranscriptArchive(env["archive"].root, source_root=env["claude"],
                                    identities=Identities())
        _sweep(archive)
        main = next(r for r in _rows(archive) if r["kind"] == "main")
        assert main["agent"] is None and main["billing_code"] is None
        assert main["git_remote"] == "github.com/Example-Org/shirka"

    def test_a_gone_cwd_has_no_remote(self, env):
        env["main"].write_text(_record(0, str(env["tmp"] / "gone")))
        _age(env["main"])
        _sweep(env["archive"])
        main = next(r for r in _rows(env["archive"]) if r["kind"] == "main")
        assert main["git_remote"] is None and main["cwd"] == str(env["tmp"] / "gone")
        assert main["git_branch"] == "main"  # the transcript's own


class TestAcceptanceSweep:
    """S001: a sweep over a fixture captures new, appended and rewritten files
    with the right versions; running it again is a no-op."""

    def _tree(self, archive):
        return {str(p.relative_to(archive.root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in archive.root.rglob("*") if p.is_file() and p.name != "state.json"}

    def test_new_appended_rewritten_and_set_aside_then_a_no_op(self, env):
        archive = env["archive"]
        repo = str(env["repo"])
        _sweep(archive)
        # new: a second conversation; appended: the main transcript;
        # rewritten: the subagent; set aside: a superseded copy of new content
        second = _second_conversation(env, repo)
        with open(env["main"], "a") as f:
            f.write(_record(3, repo, ts="2026-10-21T09:00:00.000Z"))
        env["sub"].write_text(_record(9, repo, text="rewritten"))
        aside = env["project"] / f"{SID}.jsonl.superseded-2026-10-21T10:00:00Z"
        aside.write_text(_record(0, repo, text="an older start"))
        for path in (env["main"], env["sub"], aside):
            _age(path)
        result = _sweep(archive)
        assert (result.captured, result.appended, result.versioned, result.failed) == (4, 1, 2, 0)
        current = {(r["key"], r["version"]): r for r in archive.current_rows()}
        assert current[(f"{SID}/main.jsonl", 1)]["bytes"] == env["main"].stat().st_size
        assert current[(f"{SID}/main.jsonl", 2)]["source_path"].endswith(aside.name)
        assert {v for k, v in current if k == f"{SID}/subagents/agent-a1b2.jsonl"} == {1, 2}
        assert (f"{SID2}/main.jsonl", 1) in current
        for (key, version), row in current.items():
            source = Path(expand(row["source_path"]))
            if source.exists() and not (key.endswith("agent-a1b2.jsonl") and version == 1):
                assert _content(archive, row) == source.read_bytes(), key
        manifest, tree = archive.manifest_path.read_bytes(), self._tree(archive)
        for now in (False, True, False):
            again = _sweep(archive, now=now)
            assert (again.captured, again.failed, again.unchanged) == (0, 0, again.seen)
            assert archive.manifest_path.read_bytes() == manifest
            assert self._tree(archive) == tree
        assert second.exists()


def expand(path: str) -> str:
    return ta.expand_path(path)


class TestPermissions:
    def test_directories_700_files_600(self, env):
        archive = env["archive"]
        _sweep(archive)
        transcript_queue.enqueue({"transcript_path": str(env["main"]),
                                  "hook_event_name": "SessionEnd"}, archive.root)
        for directory, dirs, files in os.walk(archive.root):
            assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700, directory
            for name in files:
                mode = stat.S_IMODE(os.stat(os.path.join(directory, name)).st_mode)
                assert mode == 0o600, name

    def test_an_open_root_is_closed(self, env):
        archive = env["archive"]
        archive.root.mkdir(mode=0o755)
        os.chmod(archive.root, 0o755)
        _sweep(archive)
        assert stat.S_IMODE(os.stat(archive.root).st_mode) == 0o700


    def test_a_loosened_tree_is_made_private_again(self, env):
        archive = env["archive"]
        _sweep(archive)
        session = archive.root / "2026-10" / SID
        loose = [session, session / "subagents", session / "main.v1.jsonl.zst",
                 archive.manifest_path, session / "meta.json", archive.root / "state.json"]
        for path in loose:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        result = _sweep(archive)
        assert result.modes_fixed == len(loose) and result.captured == 0
        _assert_private(archive.root)
        assert _sweep(archive).modes_fixed == 0

    def test_what_an_interrupted_write_left_is_removed(self, env):
        archive = env["archive"]
        _sweep(archive)
        session = archive.root / "2026-10" / SID
        (session / ".main.v1.jsonl.zst.tmp-4242-1234567").write_bytes(b"half a copy")
        (archive.root / ".state.json.tmp-4242-99").write_bytes(b"{")
        (archive.root / ".import-4242").mkdir()
        (archive.root / ".import-4242" / "member").write_bytes(b"x")
        result = _sweep(archive)
        assert result.tidied == 3
        assert sorted(p.name for p in session.iterdir()) == [
            "main.v1.jsonl.zst", "meta.json", "subagents", "tool-results"]
        assert not (archive.root / ".import-4242").exists()

    def test_private_whatever_the_umask(self, env):
        old = os.umask(0)
        try:
            archive = env["archive"]
            _sweep(archive)
            with archive.locked():
                archive.import_snapshot(_snapshot(env["tmp"], env["claude"]))
            transcript_queue.enqueue({"transcript_path": str(env["main"]),
                                      "hook_event_name": "SessionEnd"}, archive.root)
            with archive.locked():
                archive.drain_queue()
            transcript_queue.enqueue({"transcript_path": str(env["main"]),
                                      "hook_event_name": "PreCompact"}, archive.root)
        finally:
            os.umask(old)
        _assert_private(archive.root)


def _assert_private(root: Path) -> None:
    for directory, dirs, files in os.walk(root):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700, directory
        for name in files:
            assert stat.S_IMODE(os.stat(os.path.join(directory, name)).st_mode) == 0o600, name


class TestNoContentOutsideTheCopies:
    def test_secrets_are_counted_never_written(self, env, caplog):
        caplog.set_level("DEBUG")
        fake_aws = "AKIA" + "Q" * 16
        fake_header = "-----BEGIN " + "RSA PRIVATE KEY-----"
        with open(env["main"], "a") as f:
            f.write(_record(7, str(env["repo"]), text=f"{fake_aws} and {fake_header}"))
        _age(env["main"])
        archive = env["archive"]
        result = _sweep(archive)
        main = next(r for r in _rows(archive) if r["kind"] == "main")
        assert main["secrets_flagged"] == 2 and result.secrets_flagged == 2
        # The master copy is not redacted
        assert fake_aws.encode() in _content(archive, main)
        archive.load()
        assert archive.status()["secrets_flagged"] == 2
        for path in archive.root.rglob("*"):
            if path.is_file() and not path.name.endswith(".zst"):
                text = path.read_bytes()
                assert MARKER.encode() not in text, path
                assert fake_aws.encode() not in text, path
        assert MARKER not in caplog.text and fake_aws not in caplog.text

    def test_failures_log_no_content(self, env, monkeypatch, caplog):
        """What a failure logs (the module logger, the daemon's log) names the
        file and the error's type, never text the error may have quoted."""
        caplog.set_level("DEBUG")
        archive = env["archive"]
        real_scan = ta.scan_file

        def scan(path, *a, **kw):
            if path.endswith(".txt"):
                raise ValueError(f"cannot parse {MARKER}")
            return real_scan(path, *a, **kw)

        monkeypatch.setattr(ta, "scan_file", scan)
        result = _sweep(archive)
        assert result.failed == 1 and result.captured == 3
        transcript_queue.enqueue({"transcript_path": str(env["sub"])}, archive.root)
        with archive.locked():
            archive.drain_queue()
        monkeypatch.setattr(ArchiveScheduler, "enabled", staticmethod(lambda: True))
        monkeypatch.setattr(ta, "transcripts_dir", lambda: archive.root)
        log = _Log()

        def broken():
            raise RuntimeError(f"unexpected {MARKER}")

        ArchiveScheduler(log, archive_factory=broken)._run(sweep=True, drain=True)
        assert log.lines == [("warn", "transcript archive: RuntimeError")]
        ArchiveScheduler(log, archive_factory=lambda: archive)._run(sweep=True, drain=False)
        everything = caplog.text + json.dumps(log.lines)
        assert "not captured: ValueError" in caplog.text
        assert MARKER not in everything
        for path in archive.root.rglob("*"):
            if path.is_file() and not path.name.endswith(".zst"):
                assert MARKER.encode() not in path.read_bytes(), path

    def test_keys_after_a_json_escape_are_counted(self):
        line = json.dumps({"out": "x\n" + "AKIA" + "Q" * 16 + "\nkey=" + "AIza" + "b" * 35}).encode()
        assert len(ta._SECRETS_RE.findall(line)) == 2
        assert not ta._SECRETS_RE.findall(b"AKIA" + b"Q" * 20)  # too long: not a key id

    def test_cli_output_holds_no_content(self, env, monkeypatch):
        _cli_env(env, monkeypatch)
        runner = CliRunner()
        from overcode.cli._shared import app

        for args in (["archive", "sweep"], ["archive", "status"], ["archive", "status", "--json"],
                     ["archive", "find", "--repo", "github.com/example-org/shirka"]):
            out = runner.invoke(app, args)
            assert out.exit_code == 0, out.output
            assert MARKER not in out.output


# ── import ───────────────────────────────────────────────────────────────

def _snapshot(tmp_path: Path, claude: Path) -> Path:
    """A tar.zst of ``claude`` as ``tar -C ~/.claude`` writes it, with
    AppleDouble entries and a hard link, like macOS tar."""
    fh = claude / "file-history" / SID
    fh.mkdir(parents=True)
    (fh / "abc@v1").write_text("before")
    os.link(fh / "abc@v1", fh / "abc@v2")
    (claude / "history.jsonl").write_text('{"display":"x","timestamp":1}\n')
    tar_path = tmp_path / "snap.tar"
    with tarfile.open(tar_path, "w") as tar:
        for name in ("projects", "file-history", "history.jsonl"):
            tar.add(claude / name, arcname=name)
        double = b"\x00\x05\x16\x07" + b"\x00" * 60
        info = tarfile.TarInfo(f"projects/-tmp-code-shirka/._{SID}.jsonl")
        info.size = len(double)
        tar.addfile(info, io.BytesIO(double))
    subprocess.run(["zstd", "-q", "-19", str(tar_path), "-o", str(tar_path) + ".zst"], check=True)
    return Path(str(tar_path) + ".zst")


def _file_count(snapshot: Path) -> int:
    data = subprocess.run(["zstd", "-dc", str(snapshot)], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return sum(1 for m in tar if (m.isfile() or m.islnk())
                   and not os.path.basename(m.name).startswith("._"))


class TestImport:
    def test_every_file_is_a_row_and_again_adds_nothing(self, env):
        snapshot = _snapshot(env["tmp"], env["claude"])
        files = _file_count(snapshot)
        assert files == 7  # 4 session files, 2 file-history (one a hard link), history
        archive = env["archive"]
        with archive.locked():
            result = archive.import_snapshot(snapshot)
        assert result.failed == 0 and result.rows == files
        rows = _rows(archive)
        assert len(rows) == files
        assert {r["origin"] for r in rows} == {"import:snap.tar.zst"}
        link = next(r for r in rows if r["key"].endswith("abc@v2"))
        assert _content(archive, link) == b"before"
        history = next(r for r in rows if r["kind"] == "history")
        assert history["stored"] == "history/history.v1.jsonl.zst"
        main = next(r for r in rows if r["kind"] == "main")
        assert _content(archive, main) == env["main"].read_bytes()
        assert main["agent"] == "shirka-ui"
        with archive.locked():
            again = archive.import_snapshot(snapshot)
        assert again.rows == 0 and len(_rows(archive)) == files

    def test_row_count_equals_the_fixture_snapshots_file_count(self, env):
        """S001: importing the snapshot gives one manifest row per file in it
        (the real bootstrap: 3,037 files, 3,037 rows). This fixture has the
        real one's shapes: workflow subagents, tool results, set-aside copies,
        memory, file history with hard links, history, AppleDouble entries."""
        claude, repo = env["claude"], str(env["repo"])
        project = env["project"]
        (project / SID / "subagents" / "workflows" / "wf_1").mkdir(parents=True)
        (project / SID / "subagents" / "workflows" / "wf_1" / "agent-w.jsonl").write_text(
            _record(20, repo))
        (project / SID / "tool-results" / "toolu_02.json").write_text('{"n": 2}')
        (project / f"{SID}.jsonl.superseded-2026-10-20T11:00:00Z").write_text(
            _record(30, repo, text="before the rewind"))
        (project / f"{SID}.orphaned-1730000000.jsonl").write_text(_record(31, repo, text="orphan"))
        (project / "memory").mkdir()
        (project / "memory" / "MEMORY.md").write_text("- synthetic memory\n")
        _second_conversation(env, repo)
        snapshot = _snapshot(env["tmp"], claude)
        files = _file_count(snapshot)
        assert files == 13
        archive = env["archive"]
        with archive.locked():
            result = archive.import_snapshot(snapshot)
        assert (result.failed, result.skipped, result.rows, result.seen) == (0, 0, files, files)
        rows = _rows(archive)
        assert len(rows) == files and len({r["stored"] for r in rows}) == files
        assert sorted(r["version"] for r in rows if r["key"] == f"{SID}/main.jsonl") == [1, 2, 3]
        with archive.locked():
            assert archive.import_snapshot(snapshot).rows == 0
        assert _sweep(archive).captured == 0  # the live tree is what was imported

    def test_a_sweep_after_the_import_adds_only_what_changed(self, env):
        snapshot = _snapshot(env["tmp"], env["claude"])
        archive = env["archive"]
        with archive.locked():
            archive.import_snapshot(snapshot)
        before = len(_rows(archive))
        assert _sweep(archive).captured == 0
        with open(env["main"], "a") as f:
            f.write(_record(4, str(env["repo"])))
        _age(env["main"])
        result = _sweep(archive)
        assert (result.captured, result.appended) == (1, 1)
        assert len(_rows(archive)) == before + 1

    def test_not_a_snapshot(self, env):
        bad = env["tmp"] / "bad.tar.zst"
        subprocess.run(["zstd", "-q", "-o", str(bad), "-"], input=b"not a tar" * 200, check=True)
        with env["archive"].locked():
            with pytest.raises(ta.ArchiveError):
                env["archive"].import_snapshot(bad)


# ── the hooks ────────────────────────────────────────────────────────────

class TestHooks:
    def _hook_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "shirka-ui")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_SESSION_ID", "agent-id-1")
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))

    def _run(self, monkeypatch, payload):
        from overcode import hook_handler

        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        hook_handler.handle_hook_event()

    @pytest.mark.parametrize("event", ["SessionEnd", "PreCompact"])
    def test_the_event_queues_the_transcript_only(self, monkeypatch, tmp_path, event):
        self._hook_env(monkeypatch, tmp_path)
        path = str(tmp_path / f"{SID}.jsonl")
        started = time.perf_counter()
        self._run(monkeypatch, {"hook_event_name": event, "session_id": SID,
                                "transcript_path": path, "cwd": str(tmp_path)})
        assert time.perf_counter() - started < 0.2
        root = tmp_path / "oc" / "transcripts"
        (entry,) = [json.loads(line) for line in (root / "queue.jsonl").read_text().splitlines()]
        assert (entry["path"], entry["event"], entry["cc_session"]) == (path, event, SID)
        assert (entry["agent"], entry["agent_id"]) == ("shirka-ui", "agent-id-1")
        assert sorted(p.name for p in root.iterdir()) == ["queue.jsonl"]  # nothing copied
        assert stat.S_IMODE(os.stat(root).st_mode) == 0o700

    def test_precompact_is_registered_for_claude(self):
        from overcode.backends.claude_code import _build_launch_settings
        from overcode.hook_handler import OVERCODE_HOOKS

        assert ("PreCompact", "overcode hook-handler") in OVERCODE_HOOKS
        assert "PreCompact" in _build_launch_settings("/bin/overcode")["hooks"]

    def test_a_session_end_hook_process_is_fast(self, tmp_path):
        """The whole `overcode hook-handler` process for a SessionEnd, under 200 ms."""
        env = dict(os.environ, OVERCODE_SESSION_NAME="shirka-ui", OVERCODE_TMUX_SESSION="agents",
                   OVERCODE_SESSION_ID="agent-id-1", OVERCODE_DIR=str(tmp_path / "oc"),
                   OVERCODE_STATE_DIR=str(tmp_path / "oc" / "sessions"),
                   PYTHONPATH=str(Path(ta.__file__).parents[1]))
        payload = json.dumps({"hook_event_name": "SessionEnd", "session_id": SID,
                              "transcript_path": str(tmp_path / "t.jsonl")})
        command = [sys.executable, "-c",
                   "import sys; sys.argv = ['overcode', 'hook-handler'];"
                   "from overcode.entrypoint import main; main()"]
        best = None
        for _ in range(3):  # the best of three: a loaded CI box stalls one now and then
            started = time.perf_counter()
            subprocess.run(command, input=payload, text=True, env=env, check=True, timeout=10)
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
        assert best < 0.2, f"SessionEnd hook took {best * 1000:.0f} ms"
        assert transcript_queue.pending(tmp_path / "oc" / "transcripts") == 3

    def test_a_session_end_hook_is_fast_with_a_long_queue_waiting(self, tmp_path):
        """The hook appends one line: a queue the daemon has not drained (it
        is down) does not slow it, and the transcript is never opened."""
        root = tmp_path / "oc" / "transcripts"
        root.mkdir(parents=True, mode=0o700)
        line = json.dumps({"t": 1, "event": "SessionEnd", "path": "/x/" + "y" * 200}) + "\n"
        (root / "queue.jsonl").write_text(line * 50_000)
        transcript = tmp_path / "huge.jsonl"
        transcript.write_bytes(b"x")
        os.chmod(transcript, 0)  # unreadable: the hook must not read it
        env = dict(os.environ, OVERCODE_SESSION_NAME="shirka-ui", OVERCODE_TMUX_SESSION="agents",
                   OVERCODE_SESSION_ID="agent-id-1", OVERCODE_DIR=str(tmp_path / "oc"),
                   OVERCODE_STATE_DIR=str(tmp_path / "oc" / "sessions"),
                   PYTHONPATH=str(Path(ta.__file__).parents[1]))
        payload = json.dumps({"hook_event_name": "SessionEnd", "session_id": SID,
                              "transcript_path": str(transcript)})
        command = [sys.executable, "-c",
                   "import sys; sys.argv = ['overcode', 'hook-handler'];"
                   "from overcode.entrypoint import main; main()"]
        best = None
        try:
            for _ in range(3):
                started = time.perf_counter()
                subprocess.run(command, input=payload, text=True, env=env, check=True, timeout=10)
                elapsed = time.perf_counter() - started
                best = elapsed if best is None else min(best, elapsed)
        finally:
            os.chmod(transcript, 0o600)
        assert best < 0.2, f"SessionEnd hook took {best * 1000:.0f} ms"
        assert transcript_queue.pending(root) == 50_003

    def test_enqueue_never_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")
        assert not transcript_queue.enqueue({"transcript_path": "/x"}, blocker / "sub")
        assert not transcript_queue.enqueue({"transcript_path": 5}, tmp_path)

    def test_the_drain_captures_the_conversation(self, env):
        archive = env["archive"]
        transcript_queue.enqueue({"transcript_path": str(env["main"]), "session_id": SID,
                                  "hook_event_name": "SessionEnd"}, archive.root)
        transcript_queue.enqueue({"transcript_path": "/elsewhere/x.jsonl",
                                  "hook_event_name": "SessionEnd"}, archive.root)
        with archive.locked():
            result = archive.drain_queue()
        assert result.captured == 4 and result.skipped == 1
        assert transcript_queue.pending(archive.root) == 0
        assert {r["origin"] for r in _rows(archive)} == {"hook"}
        # Busy files are not deferred on the hook path, and the sweep then has nothing to do
        assert _sweep(archive).captured == 0


# ── find and cat ─────────────────────────────────────────────────────────

def _second_conversation(env, cwd: str, sid: str = SID2) -> Path:
    path = env["claude"] / "projects" / "-tmp-wt" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_record(0, cwd, ts="2026-09-30T23:00:00.000Z").replace(SID, sid))
    _age(path)
    return path


class TestFindAndCat:
    def test_find_by_remote_spans_checkout_and_worktrees(self, env):
        worktree = env["repo"] / ".claude" / "worktrees" / "feature"
        subprocess.run(["git", "-C", str(env["repo"]), "worktree", "add", "-q", "-b", "feature",
                        str(worktree)], check=True, capture_output=True)
        _second_conversation(env, str(worktree))
        gone = "77777777-8888-4999-aaaa-bbbbbbbbbbbb"
        _second_conversation(env, str(env["repo"] / ".claude" / "worktrees" / "removed"), gone)
        other = _git_repo(env["tmp"] / "code" / "other", "https://github.com/Example-Org/other")
        _second_conversation(env, str(other), "88888888-9999-4aaa-bbbb-cccccccccccc")
        archive = env["archive"]
        _sweep(archive)
        for query in ("github.com/Example-Org/shirka", "https://github.com/example-org/shirka.git",
                      str(env["repo"])):
            sessions = {r["cc_session"] for r in archive.find(repo=query, kinds=["main"])}
            assert sessions == {SID, SID2, gone}, query
        wt = next(r for r in archive.find(cc_session=SID2))
        assert wt["git_remote"] == "github.com/Example-Org/shirka" and wt["git_branch"] == "main"
        assert wt["stored"].startswith("2026-09/")  # the month of its first record

    def test_find_by_remote_spans_copies_clones_and_not_neighbours(self, env):
        """C001: worktrees, ``*-easy`` copies and scratch clones file under the
        project; a nested repository or a non-git directory beside it does not."""
        code = env["tmp"] / "code"
        git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True)
        git("clone", "-q", str(env["repo"]), str(code / "shirka-easy"))  # origin: a local path
        git("clone", "-q", str(env["repo"]), str(env["tmp"] / "scratch" / "mirror"))
        git("-C", str(env["tmp"] / "scratch" / "mirror"), "remote", "remove", "origin")
        nested = env["repo"] / "vendor" / "lib"
        nested.mkdir(parents=True)
        git("-C", str(nested), "init", "-q")
        git("-C", str(nested), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
            "commit", "-q", "--allow-empty", "-m", "another root")
        (code / "shirka-notes").mkdir()
        sessions = {
            "easy": (str(code / "shirka-easy"), "a0000000-0000-4000-8000-000000000001"),
            "gone-easy": (str(code / "shirka-easy2" / "sub"), "a0000000-0000-4000-8000-000000000002"),
            "mirror": (str(env["tmp"] / "scratch" / "mirror"), "a0000000-0000-4000-8000-000000000003"),
            "nested": (str(nested), "a0000000-0000-4000-8000-000000000004"),
            "notes": (str(code / "shirka-notes"), "a0000000-0000-4000-8000-000000000005"),
            "gone-elsewhere": (str(env["tmp"] / "gone" / "shirka-x"), "a0000000-0000-4000-8000-000000000006"),
        }
        for cwd, sid in sessions.values():
            _second_conversation(env, cwd, sid)
        archive = env["archive"]
        _sweep(archive)
        rows = {r["cc_session"]: r for r in archive.find(kinds=["main"])}
        assert rows[sessions["easy"][1]]["git_remote"] == "github.com/Example-Org/shirka"
        root_commit = rows[SID]["git_root"]
        assert len(root_commit) == 40 and rows[sessions["mirror"][1]]["git_root"] == root_commit
        assert rows[sessions["nested"][1]]["git_root"] not in (None, root_commit)
        want = {SID, *(sessions[k][1] for k in ("easy", "gone-easy", "mirror"))}
        for query in ("github.com/Example-Org/shirka", str(env["repo"])):
            assert {r["cc_session"] for r in archive.find(repo=query, kinds=["main"])} == want, query

    def test_a_local_path_remote_with_no_upstream_is_kept(self, tmp_path):
        upstream = _git_repo(tmp_path / "up", "unused")
        subprocess.run(["git", "-C", str(upstream), "remote", "remove", "origin"], check=True)
        subprocess.run(["git", "clone", "-q", str(upstream), str(tmp_path / "copy")], check=True,
                       capture_output=True)
        info = ta.git_info(str(tmp_path / "copy"), root=True)
        assert info.remote == normalize_remote(str(upstream)) and info.root

    def test_filters(self, env):
        archive = env["archive"]
        _second_conversation(env, str(env["repo"]))
        _sweep(archive)
        assert {r["cc_session"] for r in archive.find(agent="shirka-ui")} == {SID}
        assert {r["cc_session"] for r in archive.find(agent="agent-id-1")} == {SID}
        assert {r["cc_session"] for r in archive.find(billing="shirka")} == {SID}
        assert {r["cc_session"] for r in archive.find(since="2026-10-01T00:00:00")} == {SID}
        assert {r["kind"] for r in archive.find(kinds=["subagent"])} == {"subagent"}

    def test_cat(self, env):
        archive = env["archive"]
        _sweep(archive)
        out = io.BytesIO()
        assert archive.cat(SID, out) == 1
        assert out.getvalue() == env["main"].read_bytes()
        out = io.BytesIO()
        assert archive.cat(SID, out, subagents=True) == 2  # meta.json is not a transcript
        assert out.getvalue() == env["main"].read_bytes() + env["sub"].read_bytes()
        assert archive.cat("nope", io.BytesIO()) == 0

    def test_cli_find_and_cat(self, env, monkeypatch):
        _cli_env(env, monkeypatch)
        from overcode.cli._shared import app

        runner = CliRunner()
        assert runner.invoke(app, ["archive", "sweep", "--json"]).exit_code == 0
        found = runner.invoke(app, ["archive", "find", "--repo", "git@github.com:Example-Org/shirka"])
        assert found.exit_code == 0
        rows = [json.loads(line) for line in found.output.splitlines()]
        assert {r["kind"] for r in rows} == {"main", "subagent", "tool-result"}
        manifest = ta.read_manifest(Path(os.environ["OVERCODE_DIR"]) / "transcripts" / "manifest.jsonl")
        assert sorted(json.dumps(r, separators=(",", ":")) for r in manifest) == sorted(
            found.output.splitlines())  # rows exactly as the manifest has them
        cat = runner.invoke(app, ["archive", "cat", SID])
        assert cat.exit_code == 0 and cat.stdout_bytes == env["main"].read_bytes()
        missing = runner.invoke(app, ["archive", "cat", "nope"])
        assert missing.exit_code == 1
        bad = runner.invoke(app, ["archive", "find", "--since", "soon"])
        assert bad.exit_code == 2


class TestShirkaInterface:
    """find and cat are what shirka reads (S002): pin their shape."""

    # The S001 manifest row; more fields may be added, none taken away
    S001_FIELDS = {"v", "captured_at", "backend", "cc_session", "kind", "rel", "source_path",
                   "sha256", "bytes", "zst_bytes", "first_ts", "last_ts", "cc_version", "cwd",
                   "git_remote", "git_branch", "agent", "agent_id", "billing_code",
                   "parent_agent", "secrets_flagged"}

    def test_find_from_a_worktree_path_covers_the_checkout(self, env, monkeypatch):
        _cli_env(env, monkeypatch)
        from overcode.cli._shared import app

        worktree = env["repo"] / ".claude" / "worktrees" / "feature"
        subprocess.run(["git", "-C", str(env["repo"]), "worktree", "add", "-q", "-b", "feature",
                        str(worktree)], check=True, capture_output=True)
        _second_conversation(env, str(worktree))
        runner = CliRunner()
        assert runner.invoke(app, ["archive", "sweep"]).exit_code == 0
        for query in (str(worktree), str(env["repo"]), "https://github.com/Example-Org/shirka"):
            out = runner.invoke(app, ["archive", "find", "--repo", query, "--kind", "main"])
            assert out.exit_code == 0
            assert {json.loads(line)["cc_session"] for line in out.output.splitlines()} == {
                SID, SID2}, query

    def test_rows_keep_the_s001_fields_and_their_values_types(self, env, monkeypatch):
        _cli_env(env, monkeypatch)
        from overcode.cli._shared import app

        runner = CliRunner()
        runner.invoke(app, ["archive", "sweep"])
        lines = runner.invoke(app, ["archive", "find"]).output.splitlines()
        assert lines
        for line in lines:
            row = json.loads(line)
            assert self.S001_FIELDS <= set(row), self.S001_FIELDS - set(row)
            assert row["v"] == 1 and row["backend"] == "claude-code"
            assert row["kind"] in {"main", "subagent", "tool-result", "session-file"}
            assert len(row["sha256"]) == 64 and isinstance(row["bytes"], int)
            assert row["captured_at"].endswith("Z") and row["source_path"].startswith(("~", "/"))
            assert line == json.dumps(row, separators=(",", ":"))  # compact, one line
        main = next(json.loads(x) for x in lines if json.loads(x)["kind"] == "main")
        assert main["rel"] == "main.v1.jsonl.zst"
        assert main["stored"] == f"2026-10/{SID}/{main['rel']}"


def _cli_env(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env["claude"]))
    monkeypatch.setenv("OVERCODE_DIR", str(env["tmp"] / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(env["tmp"] / "oc" / "sessions"))
    monkeypatch.setattr(ta.Identities, "load", classmethod(lambda cls: Identities()))


# ── identity, config, the daemon's scheduler ─────────────────────────────

class TestIdentities:
    def test_live_and_archived_records(self):
        ids = Identities.from_records([
            {"id": "a", "name": "old-name", "stats": {"conversations": {SID: {}}},
             "billing_code": "proj"},
            {"id": "b", "name": "child", "parent_session_id": "a",
             "active_agent_session_id": SID2, "claude_session_ids": ["x"]},
            {"id": "a", "name": "new-name", "stats": {"conversations": {SID: {}}}},
        ])
        assert ids.lookup(SID)["agent"] == "new-name"
        assert ids.lookup(SID)["billing_code"] == "new-name"  # no code: the name, as the ledger
        assert ids.lookup(SID2) == ids.lookup("x")
        assert ids.lookup(SID2)["parent_agent"] == "new-name"
        assert ids.lookup("unknown") == {}

    def test_spend_alone_names_an_agent_only_when_one_record_has_it(self):
        """Old records list other agents' conversations in their spend: those
        many records spend on belong to none of them; an owned id beats spend."""
        crowd = [{"id": f"old-{i}", "name": f"old-{i}",
                  "stats": {"conversations": {SID: {}, SID2: {}}}} for i in range(13)]
        owner = {"id": "own", "name": "owner", "agent_session_ids": [SID2],
                 "stats": {"conversations": {SID2: {}}}}
        ids = Identities.from_records([*crowd, owner])
        assert ids.lookup(SID) == {}
        assert ids.lookup(SID2)["agent"] == "owner"
        assert ids.of_agent("old-3")["agent"] == "old-3"

    def test_the_hook_that_queued_a_conversation_names_its_agent(self, env):
        archive = TranscriptArchive(env["archive"].root, source_root=env["claude"],
                                    identities=Identities.from_records([
                                        {"id": "agent-id-9", "name": "queuer", "billing_code": "q",
                                         "parent_session_id": "agent-id-0"},
                                        {"id": "agent-id-0", "name": "shirka"},
                                        {"id": "other", "name": "other", "agent_session_ids": [SID]},
                                    ]))
        transcript_queue.enqueue({"transcript_path": str(env["main"]), "session_id": SID,
                                  "hook_event_name": "SessionEnd"}, archive.root)
        lines = transcript_queue.queue_path(archive.root).read_text().splitlines()
        entry = dict(json.loads(lines[0]), agent="queuer", agent_id="agent-id-9")
        transcript_queue.queue_path(archive.root).write_text(json.dumps(entry) + "\n")
        with archive.locked():
            archive.drain_queue()
        main = next(r for r in _rows(archive) if r["kind"] == "main")
        assert (main["agent"], main["agent_id"], main["billing_code"], main["parent_agent"]) == (
            "queuer", "agent-id-9", "q", "shirka")


class TestConfig:
    def test_defaults_and_values(self, monkeypatch):
        from overcode import config

        monkeypatch.setattr(config, "_cached_config", lambda: {})
        assert config.get_transcript_archive_config() == {"sweep": True, "max_bytes": 0}
        monkeypatch.setattr(config, "_cached_config", lambda: {
            "archive": {"retention_months": 3, "transcripts": {"sweep": False, "max_bytes": "2GB"}}})
        assert config.get_transcript_archive_config() == {"sweep": False, "max_bytes": 2 * 1024 ** 3}
        assert config.get_archive_config() == {"retention_months": 3}

    def test_over_the_cap_nothing_new_is_taken(self, env):
        archive = TranscriptArchive(env["archive"].root, source_root=env["claude"],
                                    identities=Identities(), max_bytes=1)
        result = _sweep(archive)
        assert result.captured == 1 and result.over_cap == 3  # the first copy fills it
        assert _sweep(archive).over_cap == 3  # and the rest are retried, never dropped


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(("info", message))

    def warn(self, message):
        self.lines.append(("warn", message))


class TestScheduler:
    def _scheduler(self, env, monkeypatch, **kw):
        monkeypatch.setattr(ArchiveScheduler, "enabled", staticmethod(lambda: True))
        monkeypatch.setattr(ta, "transcripts_dir", lambda: env["archive"].root)
        clock = {"t": 1000.0}
        log = _Log()
        scheduler = ArchiveScheduler(log, archive_factory=lambda: env["archive"],
                                     clock=lambda: clock["t"], **kw)
        return scheduler, clock, log

    def test_sweeps_hourly_on_its_thread(self, env, monkeypatch):
        scheduler, clock, log = self._scheduler(env, monkeypatch)
        scheduler.tick()
        scheduler.thread.join(30)
        assert len(_rows(env["archive"])) == 4
        assert any("4 captured" in m for _, m in log.lines)
        thread = scheduler.thread
        clock["t"] += 60
        scheduler.tick()  # queue checked (empty), the sweep not due
        assert scheduler.thread is thread
        clock["t"] += ta.SWEEP_INTERVAL_SECONDS
        scheduler.tick()  # due, but this machine swept within the hour
        assert scheduler.thread is thread

    def test_drains_the_queue_within_a_minute(self, env, monkeypatch):
        scheduler, clock, _ = self._scheduler(env, monkeypatch)
        scheduler.last_sweep_check = clock["t"]
        scheduler.last_queue_check = clock["t"]
        transcript_queue.enqueue({"transcript_path": str(env["main"])}, env["archive"].root)
        scheduler.tick()
        assert scheduler.thread is None
        clock["t"] += ta.QUEUE_CHECK_SECONDS
        scheduler.tick()
        scheduler.thread.join(30)
        assert transcript_queue.pending(env["archive"].root) == 0
        assert {r["origin"] for r in _rows(env["archive"])} == {"hook"}

    def test_another_holder_of_the_lock_is_waited_for_next_time(self, env, monkeypatch):
        scheduler, _, log = self._scheduler(env, monkeypatch)
        with env["archive"].locked():
            scheduler._run(sweep=True, drain=False)
        assert not _rows(env["archive"]) and not log.lines

    def test_off(self, env, monkeypatch):
        scheduler, _, _ = self._scheduler(env, monkeypatch)
        monkeypatch.setattr(ArchiveScheduler, "enabled", staticmethod(lambda: False))
        scheduler.tick()
        assert scheduler.thread is None

    def test_warns_at_80_percent_of_the_cap(self, env, monkeypatch):
        scheduler, _, log = self._scheduler(env, monkeypatch)
        env["archive"].max_bytes = 10 ** 9
        env["archive"].stored_bytes = int(0.85 * 10 ** 9)
        scheduler._check_cap(env["archive"])
        assert any(level == "warn" and "85%" in m for level, m in log.lines)

    def test_test_daemons_have_none(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        assert MonitorDaemon.for_test(tmp_path / "state").transcripts is None


class TestArchiveRoot:
    def test_env_and_root_option_put_the_archive_elsewhere(self, env, monkeypatch):
        from overcode.paths import transcripts_dir

        _cli_env(env, monkeypatch)
        scratch = env["tmp"] / "scratch"
        monkeypatch.setenv("OVERCODE_TRANSCRIPTS_DIR", str(scratch))
        assert transcripts_dir() == scratch
        from overcode.cli._shared import app

        runner = CliRunner()
        elsewhere = env["tmp"] / "elsewhere"
        assert runner.invoke(app, ["archive", "--root", str(elsewhere), "sweep"]).exit_code == 0
        assert len(_rows(TranscriptArchive(elsewhere))) == 4
        assert not scratch.exists()
        # Without --root the next command is back on the default
        assert runner.invoke(app, ["archive", "sweep"]).exit_code == 0
        assert len(_rows(TranscriptArchive(scratch))) == 4
        assert not (env["tmp"] / "oc" / "transcripts").exists()
