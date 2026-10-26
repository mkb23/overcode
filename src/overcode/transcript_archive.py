"""The transcript archive: every Claude Code transcript, kept for ever.

Claude Code deletes its transcripts after ``cleanupPeriodDays`` (30 by
default). This archive keeps a byte-exact, zstd-compressed, private copy of
each one, findable by conversation, agent, billing code and repository,
whether or not overcode launched the session (shirka spec S001)::

    ~/.overcode/transcripts/                  700
      manifest.jsonl                          append-only, one row per captured file version
      state.json                              the sweep's stat cache and last result
      queue.jsonl                             transcript paths the hooks queued
      YYYY-MM/<cc_session>/                   month of the session's first record
        main.v<N>.jsonl.zst
        subagents/<path>.v<N>.<ext>.zst
        tool-results/<name>.v<N>.<ext>.zst
        file-history/<name>.v<N>.zst          (from the bootstrap snapshot)
        <other session files>.v<N>.<ext>.zst
        meta.json                             the session's latest manifest row
      projects/<project>/<path>.v<N>.<ext>.zst  project files (memory, indexes)
      history/history.v<N>.jsonl.zst          ~/.claude/history.jsonl (snapshot)

Every file is mode 600 and every directory 700. The copies are never
redacted; the manifest holds no content, only a count of key-like strings
(``secrets_flagged``). Nothing here logs or prints content either.

Versioning. Each archived file has a *key* (its session and its path in
the session, with any ``.superseded-*`` / ``.orphaned-*`` marker taken out)
and versions 1, 2, ... A source that grew by appending only replaces the
version it was captured as; one rewritten, shrunk or changed in place, or
a set-aside copy whose content is new, adds version N+1 and the old one is
kept. Content already archived under the key is not stored twice.

Capture. ``sweep`` walks ``~/.claude/projects``: a file whose (size, mtime)
changed since the last sweep is hashed and, if its content is new, copied.
A file still being written is read as a snapshot of its size at the stat
and taken again next sweep. An append to a file written in the last
``QUIET_SECONDS`` waits until it is quiet (or a day has passed since its
last copy), so a busy transcript is not recompressed every hour; new
files are copied at once. The hooks' queue (``transcript_queue``) is
drained without that wait. The monitor daemon runs both on a background
thread (``ArchiveScheduler``); ``overcode archive sweep`` runs one in the
foreground. One process at a time holds the archive's lock.

Identity. A conversation is mapped to the overcode agent that owned it
(``agent_session_ids`` of the live records, ``stats.conversations`` of the
archived ones; see :class:`Identities`), its billing code and its parent;
the git remote, branch and root commit come from the transcript's ``cwd``
at capture time (null when it no longer exists; the transcript's own
``gitBranch`` wins for the branch). :func:`match_repo` files worktrees,
clones and ``*-easy`` copies under their project.

``find`` and ``cat`` are what shirka reads (S002): keep their output stable.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (BinaryIO, Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional,
                    Tuple)

from . import transcript_queue
from .paths import transcripts_dir

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

ROW_VERSION = 1
BACKEND = "claude-code"
MANIFEST_FILE = "manifest.jsonl"
STATE_FILE = "state.json"
LOCK_FILE = ".lock"
META_FILE = "meta.json"

ZSTD_LEVEL = 19
# Files larger than this are compressed with --long (a 128 MB window)
LONG_THRESHOLD = 64 * 1024 * 1024
LONG_WINDOW_LOG = 27
CHUNK = 1024 * 1024

# An append to a file written more recently than this waits for the file
# to go quiet, unless its last copy is older than MAX_DEFER_SECONDS
QUIET_SECONDS = 3600.0
MAX_DEFER_SECONDS = 86400.0
SWEEP_INTERVAL_SECONDS = 3600.0
QUEUE_CHECK_SECONDS = 30.0
# Warn when the archive reaches this share of archive.transcripts.max_bytes
ALERT_FRACTION = 0.8

KIND_MAIN = "main"
KIND_SUBAGENT = "subagent"
KIND_TOOL_RESULT = "tool-result"
KIND_SESSION_FILE = "session-file"
KIND_FILE_HISTORY = "file-history"
KIND_PROJECT_FILE = "project-file"
KIND_HISTORY = "history"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(rf"^{_UUID}$")
# The marker Claude Code puts on a transcript it set aside
_SET_ASIDE_RE = re.compile(r"\.(?:superseded|orphaned)-[^/]*?(?=\.jsonl$|$)")
_MAIN_RE = re.compile(rf"^({_UUID})(.*)$")

# Key-like strings, after gitleaks' rules (a subset). Only ever counted. No
# leading \b on most: in a JSONL line a key often follows an escape (\n).
SECRET_RULES: Dict[str, bytes] = {
    "aws-access-key": rb"(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}(?![A-Za-z0-9])",
    "github-token": rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b",
    "github-fine-grained-pat": rb"\bgithub_pat_[A-Za-z0-9_]{82}\b",
    "gitlab-pat": rb"\bglpat-[0-9A-Za-z_-]{20}\b",
    "anthropic-key": rb"\bsk-ant-(?:api|admin)\d\d-[A-Za-z0-9_-]{80,}",
    "openai-key": rb"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}",
    "openai-legacy-key": rb"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b",
    "slack-token": rb"\bxox[baprs]-[0-9A-Za-z-]{10,}",
    "google-api-key": rb"AIza[0-9A-Za-z_-]{35}",
    "stripe-key": rb"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}\b",
    "npm-token": rb"\bnpm_[A-Za-z0-9]{36}\b",
    "huggingface-token": rb"\bhf_[A-Za-z]{34}\b",
    "private-key": rb"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY-----",
}
_SECRETS_RE = re.compile(b"|".join(b"(?:" + p + b")" for p in SECRET_RULES.values()))

# Record fields, read without parsing the line: a key in a nested JSON
# string has its quotes escaped and does not match
_TS_RE = re.compile(rb'"timestamp" ?: ?"(\d{4}-\d\d-\d\dT[^"]{1,40})"')
_FIELD_RES = {
    "cwd": re.compile(rb'"cwd" ?: ?"((?:[^"\\]|\\.){1,4096})"'),
    "cc_version": re.compile(rb'"version" ?: ?"([0-9][^"]{0,40})"'),
    "git_branch": re.compile(rb'"gitBranch" ?: ?"((?:[^"\\]|\\.){0,512})"'),
}
# AppleDouble files macOS tar adds for extended attributes ("._name")
_APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"


class ArchiveError(RuntimeError):
    """The archive cannot do what was asked (no zstd, a damaged snapshot)."""


class ArchiveBusy(ArchiveError):
    """Another process holds the archive's lock."""


# ── small helpers ────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def claude_dir() -> Path:
    """Claude Code's own directory: ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def display_path(path: str) -> str:
    """``path`` with the home directory written ``~``."""
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def expand_path(path: str) -> str:
    return os.path.expanduser(path) if path.startswith("~") else path


def zstd_binary() -> Optional[str]:
    return shutil.which("zstd")


def _need_zstd() -> str:
    binary = zstd_binary()
    if not binary:
        raise ArchiveError("zstd is not installed (brew install zstd / apt install zstd)")
    return binary


def ensure_private_dir(path: Path, root: Path) -> None:
    """Make ``path`` (under ``root``) and any missing parents, each mode 700."""
    missing = []
    current = path
    while not current.is_dir():
        missing.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
        os.chmod(directory, 0o700)


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, mode 600."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def normalize_remote(url: Optional[str]) -> Optional[str]:
    """A git remote as ``host/owner/repo``: no scheme, user, port or ``.git``.

    ``git@github.com:Org/repo.git``, ``https://user@github.com/Org/repo``
    and ``ssh://git@github.com:22/Org/repo.git`` are all
    ``github.com/Org/repo``. The host is lower-cased. None for nothing.
    """
    if not url or not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text)
    if scheme:
        text = text[scheme.end():]
        host, _, path = text.partition("/")
    else:
        scp = re.match(r"^(?:[^@/]+@)?([^:/]+):(?!/)(.*)$", text)
        if scp:
            host, path = scp.group(1), scp.group(2)
        else:
            host, _, path = text.partition("/")
    host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0].lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.rstrip("/")
    return f"{host}/{path}" if path else host or None


def same_remote(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two normalised remotes name one repository (case-insensitively)."""
    return bool(a and b) and a.casefold() == b.casefold()


def _git(args: List[str], cwd: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


class GitInfo(NamedTuple):
    """What git says about a directory: all None when it is gone or not in a repo."""
    remote: Optional[str] = None   # normalised (normalize_remote)
    branch: Optional[str] = None
    top: Optional[str] = None      # the worktree's root
    root: Optional[str] = None     # the repository's root commit, when asked for


def _first_remote(cwd: str) -> Optional[str]:
    remote = _git(["remote", "get-url", "origin"], cwd)
    if not remote:
        names = _git(["remote"], cwd)
        first = names.split()[0] if names else None
        remote = _git(["remote", "get-url", first], cwd) if first else None
    return remote


def _upstream_remote(remote: Optional[str], top: str, depth: int = 0) -> Optional[str]:
    """``remote``, or when it is a local path (a clone of a clone), that repository's own."""
    if not remote:
        return None
    if remote.startswith("file://"):
        local = remote[len("file://"):]
    elif remote.startswith(("/", "~", ".")):
        local = os.path.join(top, os.path.expanduser(remote))
    else:
        return remote
    if depth < 3 and os.path.isdir(local):
        upstream = _first_remote(local)
        if upstream:
            return _upstream_remote(upstream, local, depth + 1)
    return remote


def git_info(cwd: Optional[str], *, root: bool = False) -> GitInfo:
    """What git says about ``cwd`` (``root``: also the root commit); Nones when it is gone.

    A remote that is a local path (``git clone ~/Code/x x-easy``) stands for
    that repository's own remote, so a copy is filed under the project.
    """
    if not cwd or not os.path.isdir(cwd):
        return GitInfo()
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    if not top:
        return GitInfo()
    remote = _upstream_remote(_first_remote(cwd), top)
    branch = _git(["branch", "--show-current"], cwd)
    first = None
    if root:
        roots = _git(["rev-list", "--max-parents=0", "HEAD"], cwd)
        first = min(roots.split()) if roots else None
    return GitInfo(normalize_remote(remote), branch, top, first)


# ── what a file is ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileRef:
    """Where a file of Claude's directory goes in the archive.

    ``rel`` is its path under Claude's directory (``projects/<p>/<id>.jsonl``);
    ``key`` names the archived file across its versions; ``inner`` is its
    path inside its session (or project) directory without the version.
    """
    rel: str
    kind: str
    key: str
    inner: str
    cc_session: Optional[str] = None
    project: Optional[str] = None


def classify(rel: str) -> Optional[FileRef]:
    """The :class:`FileRef` of ``rel`` (a path under Claude's directory), or None."""
    parts = rel.split("/")
    if parts[-1].startswith("._") or any(not p or p in (".", "..") for p in parts):
        return None
    if parts == ["history.jsonl"]:
        return FileRef(rel, KIND_HISTORY, "history.jsonl", "history.jsonl")
    if parts[0] == "file-history" and len(parts) >= 3 and _UUID_RE.match(parts[1]):
        inner = "file-history/" + "/".join(parts[2:])
        return FileRef(rel, KIND_FILE_HISTORY, f"{parts[1]}/{inner}", inner, cc_session=parts[1])
    if parts[0] != "projects" or len(parts) < 3:
        return None
    project = parts[1]
    if len(parts) == 3:
        name = parts[2]
        main = _MAIN_RE.match(name)
        if main and name.startswith(main.group(1)) and (
                name == main.group(1) + ".jsonl" or _SET_ASIDE_RE.search(main.group(2))):
            cc_session = main.group(1)
            return FileRef(rel, KIND_MAIN, f"{cc_session}/main.jsonl", "main.jsonl",
                           cc_session=cc_session, project=project)
    if len(parts) >= 4 and _UUID_RE.match(parts[2]):
        cc_session = parts[2]
        inner = _SET_ASIDE_RE.sub("", "/".join(parts[3:]))
        kind = {"subagents": KIND_SUBAGENT, "tool-results": KIND_TOOL_RESULT}.get(
            parts[3], KIND_SESSION_FILE)
        return FileRef(rel, kind, f"{cc_session}/{inner}", inner,
                       cc_session=cc_session, project=project)
    inner = "/".join(parts[2:])
    return FileRef(rel, KIND_PROJECT_FILE, f"project:{project}/{inner}", inner, project=project)


def versioned_name(name: str, version: int) -> str:
    """``agent-x.jsonl`` -> ``agent-x.v2.jsonl.zst``; ``a@v1`` -> ``a@v1.v2.zst``."""
    head, dot, rest = name.partition(".") if not name.startswith(".") else (name, "", "")
    if not dot:
        return f"{name}.v{version}.zst"
    return f"{head}.v{version}.{rest}.zst"


def stored_path(ref: FileRef, version: int, month: Optional[str]) -> str:
    """Where version ``version`` of ``ref`` is kept, under the archive root."""
    directory, _, name = ref.inner.rpartition("/")
    leaf = versioned_name(name, version)
    if ref.kind == KIND_MAIN:
        leaf = f"main.v{version}.jsonl.zst"
    inner = f"{directory}/{leaf}" if directory else leaf
    if ref.kind == KIND_HISTORY:
        return f"history/{leaf}"
    if ref.cc_session:
        return f"{month}/{ref.cc_session}/{inner}"
    return f"projects/{ref.project}/{inner}"


# ── reading a snapshot ───────────────────────────────────────────────────

@dataclass
class Scan:
    """What one pass over a file's bytes found: never content."""
    sha256: str = ""
    bytes: int = 0
    prefix_sha256: Optional[str] = None
    secrets: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)


class _Scanner:
    """Hashes, counts key-like strings and reads record fields, a chunk at a time.

    Lines are scanned whole (a key never spans a JSONL line), so the tail
    of a chunk waits for the next one. ``prefix_at`` also hashes the first
    that many bytes: the content an earlier capture of the file had, if it
    only grew since.
    """

    def __init__(self, *, records: bool, prefix_at: Optional[int] = None):
        self.hash = hashlib.sha256()
        self.records = records
        self.prefix_at = prefix_at
        self.scan = Scan()
        self._carry = b""

    def feed(self, chunk: bytes) -> None:
        if self.prefix_at is not None and self.scan.prefix_sha256 is None:
            before = self.scan.bytes
            if before + len(chunk) >= self.prefix_at:
                cut = self.prefix_at - before
                self.hash.update(chunk[:cut])
                self.scan.prefix_sha256 = self.hash.hexdigest()
                self.hash.update(chunk[cut:])
            else:
                self.hash.update(chunk)
        else:
            self.hash.update(chunk)
        self.scan.bytes += len(chunk)
        data = self._carry + chunk
        end = data.rfind(b"\n")
        if end < 0:
            self._carry = data
            if len(self._carry) > 64 * CHUNK:  # a huge line: scan it in pieces
                self._lines(self._carry)
                self._carry = b""
            return
        self._lines(data[:end + 1])
        self._carry = data[end + 1:]

    def _lines(self, block: bytes) -> None:
        scan = self.scan
        scan.secrets += sum(1 for _ in _SECRETS_RE.finditer(block))
        if not self.records:
            return
        for match in _TS_RE.finditer(block):
            ts = match.group(1).decode("ascii", "replace")
            if scan.first_ts is None or ts < scan.first_ts:
                scan.first_ts = ts
            if scan.last_ts is None or ts > scan.last_ts:
                scan.last_ts = ts
        for name, pattern in _FIELD_RES.items():
            if name in scan.fields and name != "git_branch":
                continue
            found = None
            for found in pattern.finditer(block):
                if name != "git_branch":
                    break
            if found is not None:
                try:
                    value = json.loads(b'"' + found.group(1) + b'"')
                except ValueError:
                    continue
                if isinstance(value, str) and (value or name != "git_branch"):
                    scan.fields[name] = value

    def finish(self) -> Scan:
        if self._carry:
            self._lines(self._carry)
            self._carry = b""
        if self.prefix_at is not None and self.scan.prefix_sha256 is None:
            if self.scan.bytes == self.prefix_at:
                self.scan.prefix_sha256 = self.hash.hexdigest()
        self.scan.sha256 = self.hash.hexdigest()
        return self.scan


def _is_records(ref: FileRef) -> bool:
    return ref.inner.endswith(".jsonl")


def _read_exactly(f: BinaryIO, size: int) -> Iterator[bytes]:
    """Up to ``size`` bytes of ``f``, a chunk at a time (a snapshot of a growing file)."""
    left = size
    while left > 0:
        chunk = f.read(min(CHUNK, left))
        if not chunk:
            return
        left -= len(chunk)
        yield chunk


def scan_file(path: str, size: int, ref: FileRef, prefix_at: Optional[int] = None) -> Scan:
    scanner = _Scanner(records=_is_records(ref), prefix_at=prefix_at)
    with open(path, "rb") as f:
        for chunk in _read_exactly(f, size):
            scanner.feed(chunk)
    return scanner.finish()


class _Compressor:
    """A ``zstd -19`` process writing one archive file, mode 600, atomically."""

    def __init__(self, dest: Path, size: int, *, gentle: bool):
        self.dest = dest
        self.tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        command = [_need_zstd(), f"-{ZSTD_LEVEL}", "-q", "-c"]
        if size > LONG_THRESHOLD:
            command.append(f"--long={LONG_WINDOW_LOG}")
        if gentle and shutil.which("nice"):
            command = ["nice", "-n", "10", *command]
        fd = os.open(self.tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self.out = os.fdopen(fd, "wb")
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=self.out,
                                         stderr=subprocess.DEVNULL)
        except BaseException:
            self.out.close()
            os.unlink(self.tmp)
            raise

    def write(self, chunk: bytes) -> None:
        self.proc.stdin.write(chunk)

    def commit(self) -> int:
        """Finish, fsync and move into place; the compressed size."""
        self.proc.stdin.close()
        code = self.proc.wait()
        self.out.flush()
        os.fsync(self.out.fileno())
        self.out.close()
        if code != 0:
            os.unlink(self.tmp)
            raise ArchiveError(f"zstd exited {code}")
        os.chmod(self.tmp, 0o600)
        os.replace(self.tmp, self.dest)
        return self.dest.stat().st_size

    def abort(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.kill()
        self.proc.wait()
        self.out.close()
        try:
            os.unlink(self.tmp)
        except OSError:
            pass


def decompress_to(path: Path, out: BinaryIO) -> None:
    """Write the content of the archive file ``path`` to ``out``."""
    proc = subprocess.Popen([_need_zstd(), "-dc", "-q", "--long=31", str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            chunk = proc.stdout.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
    finally:
        proc.stdout.close()
        code = proc.wait()
    if code != 0:
        raise ArchiveError(f"zstd could not read {path.name}")


# ── identity ─────────────────────────────────────────────────────────────

class Identities:
    """Which overcode agent each conversation belonged to.

    An agent's own conversation ids (``agent_session_ids``, the active id)
    say so; ``stats.conversations`` (its spend, per conversation) counts
    only when no record owns the conversation and one record alone has it
    there. Old records carry other agents' conversations in their spend
    (a daemon once adopted every conversation of a directory: 13 records
    of one checkout list the same 392), so a conversation two records
    spend on belongs to neither: null beats a guess.
    """

    def __init__(self, by_session: Optional[Dict[str, dict]] = None,
                 by_agent: Optional[Dict[str, dict]] = None):
        self.by_session: Dict[str, dict] = by_session or {}
        self.by_agent: Dict[str, dict] = by_agent or {}

    def lookup(self, cc_session: Optional[str]) -> dict:
        return self.by_session.get(cc_session or "", {})

    def of_agent(self, agent_id: Optional[str]) -> dict:
        """The identity of the agent with overcode id ``agent_id`` ({} if unknown)."""
        return self.by_agent.get(agent_id or "", {})

    @classmethod
    def load(cls) -> "Identities":
        """From sessions.json and the session archive; empty if neither reads."""
        try:
            from .session_manager import SessionManager

            manager = SessionManager(skip_git_detection=True)
            records: List[dict] = list(manager.iter_archived_records())
            records.extend(manager._load_state().values())
        except Exception as e:  # identity is best-effort; capture is not
            logger.warning("transcript archive: agent records not read: %s", type(e).__name__)
            return cls()
        return cls.from_records(records)

    @classmethod
    def from_records(cls, records: Iterable[dict]) -> "Identities":
        """Later records of one agent win (archive oldest first, then the live ones)."""
        by_id: Dict[str, dict] = {}
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                by_id[record["id"]] = record
        by_agent: Dict[str, dict] = {}
        owned: Dict[str, dict] = {}
        spent: Dict[str, List[dict]] = {}
        for record in by_id.values():
            parent = by_id.get(record.get("parent_session_id") or "")
            identity = {
                "agent": record.get("name"),
                "agent_id": record.get("id"),
                "billing_code": record.get("billing_code") or record.get("name"),
                "billing_code_id": record.get("billing_code_id"),
                "parent_agent": parent.get("name") if parent else None,
            }
            by_agent[record["id"]] = identity
            ids = set(record.get("agent_session_ids") or record.get("claude_session_ids") or [])
            for key in ("active_agent_session_id", "active_claude_session_id"):
                if isinstance(record.get(key), str):
                    ids.add(record[key])
            for cc_session in ids:
                if isinstance(cc_session, str):
                    owned[cc_session] = identity
            stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
            conversations = stats.get("conversations")
            if isinstance(conversations, dict):
                for cc_session in conversations:
                    if isinstance(cc_session, str) and cc_session not in ids:
                        spent.setdefault(cc_session, []).append(identity)
        by_session = {cc: found[0] for cc, found in spent.items() if len(found) == 1}
        by_session.update(owned)
        return cls(by_session, by_agent)


# ── the archive ──────────────────────────────────────────────────────────

@dataclass
class Result:
    """Counts from one sweep, drain or import. Never content."""
    seen: int = 0
    captured: int = 0
    appended: int = 0
    versioned: int = 0
    unchanged: int = 0
    deferred: int = 0
    skipped: int = 0
    failed: int = 0
    over_cap: int = 0
    modes_fixed: int = 0
    rows: int = 0
    bytes: int = 0
    zst_bytes: int = 0
    secrets_flagged: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return dict(vars(self))


class TranscriptArchive:
    """The archive under ``root`` (default ``~/.overcode/transcripts``)."""

    def __init__(self, root: Optional[Path] = None, *, source_root: Optional[Path] = None,
                 identities: Optional[Identities] = None, max_bytes: int = 0,
                 gentle: bool = False, clock: Callable[[], float] = time.time):
        self.root = Path(root) if root is not None else transcripts_dir()
        self.source_root = Path(source_root) if source_root is not None else claude_dir()
        self._identities = identities
        self.max_bytes = max_bytes
        self.gentle = gentle
        self.clock = clock
        self._git_cache: Dict[str, GitInfo] = {}
        # Built from the manifest by load()
        self.latest_by_source: Dict[str, dict] = {}
        self.versions: Dict[str, Dict[int, dict]] = {}
        self.months: Dict[str, str] = {}
        self.by_stored: Dict[str, dict] = {}
        self.context: Dict[str, dict] = {}
        self.state: dict = {}
        self.stored_bytes = 0

    # ── files and the lock ──────────────────────────────────────────

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILE

    def ensure_root(self) -> None:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_dir(self.root, self.root)
        os.chmod(self.root, 0o700)

    @contextmanager
    def locked(self, *, wait: bool = True):
        """The archive's lock: one sweep, drain or import at a time, machine-wide."""
        self.ensure_root()
        fd = os.open(self.root / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if HAS_FCNTL:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
                except BlockingIOError:
                    raise ArchiveBusy("another process is writing the transcript archive")
            self.load()
            yield self
        finally:
            os.close(fd)

    def fix_modes(self) -> int:
        """Make every directory under the root 700 and every file 600; how many changed.

        New paths are made private as they are written; this catches what
        was put here another way (a restore, a copy) or loosened since.
        """
        fixed = 0
        for directory, dirs, files in os.walk(self.root):
            for name, mode in [(d, 0o700) for d in dirs] + [(f, 0o600) for f in files]:
                path = os.path.join(directory, name)
                try:
                    st = os.lstat(path)
                    if not stat.S_ISLNK(st.st_mode) and stat.S_IMODE(st.st_mode) != mode:
                        os.chmod(path, mode)
                        fixed += 1
                except OSError:
                    continue
        return fixed

    def identities(self) -> Identities:
        if self._identities is None:
            self._identities = Identities.load()
        return self._identities

    # ── the manifest ────────────────────────────────────────────────

    def rows(self) -> Iterator[dict]:
        """Every manifest row, oldest first (a torn last line is skipped)."""
        yield from read_manifest(self.manifest_path)

    def load(self) -> None:
        """Index the manifest and read the sweep state."""
        self.latest_by_source = {}
        self.versions = {}
        self.months = {}
        self.context = {}
        self.by_stored = {}
        for row in self.rows():
            key = row.get("key")
            version = row.get("version")
            if not isinstance(key, str) or not isinstance(version, int):
                continue
            self.versions.setdefault(key, {})[version] = row
            source = row.get("source_path")
            if isinstance(source, str):
                self.latest_by_source[source] = row
            cc_session, path = row.get("cc_session"), row.get("stored")
            if cc_session and isinstance(path, str) and "/" in path:
                self.months.setdefault(cc_session, path.split("/", 1)[0])
            if cc_session and row.get("kind") == KIND_MAIN:
                self.context[cc_session] = _context_of(row)
            if isinstance(path, str):
                self.by_stored[path] = row
        self.stored_bytes = sum(int(r.get("zst_bytes") or 0) for r in self.by_stored.values())
        try:
            self.state = json.loads((self.root / STATE_FILE).read_text())
        except (OSError, ValueError):
            self.state = {}
        for name in ("stat", "alias"):
            if not isinstance(self.state.get(name), dict):
                self.state[name] = {}

    @property
    def aliases(self) -> Dict[str, str]:
        """Sources whose content was already archived from another source: source -> stored."""
        return self.state["alias"]

    def previous(self, source_path: str) -> Optional[dict]:
        """The row of what ``source_path`` was last captured as (its own, or the one it equals)."""
        if source_path in self.aliases:
            row = self.by_stored.get(self.aliases[source_path])
            if row is not None:
                return row
        return self.latest_by_source.get(source_path)

    def save_state(self) -> None:
        _write_private(self.root / STATE_FILE,
                       json.dumps(self.state, separators=(",", ":")).encode("utf-8"))

    def _append_rows(self, rows: List[dict]) -> None:
        if not rows:
            return
        data = b"".join((json.dumps(r, separators=(",", ":")) + "\n").encode("utf-8")
                        for r in rows)
        fd = os.open(self.manifest_path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            size = os.fstat(fd).st_size
            if size:
                os.lseek(fd, size - 1, os.SEEK_SET)
                if os.read(fd, 1) != b"\n":
                    data = b"\n" + data  # a torn last line keeps to itself
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        for row in rows:
            self.versions.setdefault(row["key"], {})[row["version"]] = row
            self.latest_by_source[row["source_path"]] = row
            self.by_stored[row["stored"]] = row
            self.aliases.pop(row["source_path"], None)
            if row.get("cc_session"):
                self.months.setdefault(row["cc_session"], row["stored"].split("/", 1)[0])
                self._write_meta(row)

    def _write_meta(self, row: dict) -> None:
        """The session's ``meta.json``: its latest row, the main one's if it has one."""
        if row["kind"] != KIND_MAIN:
            meta = self.root / row["stored"].split("/", 1)[0] / row["cc_session"] / META_FILE
            if meta.exists():
                try:
                    if json.loads(meta.read_bytes()).get("kind") == KIND_MAIN:
                        return
                except (OSError, ValueError):
                    pass
        directory = self.root / row["stored"].split("/", 1)[0] / row["cc_session"]
        ensure_private_dir(directory, self.root)
        _write_private(directory / META_FILE, json.dumps(row, indent=1).encode("utf-8"))

    # ── capture ─────────────────────────────────────────────────────

    def _month_for(self, ref: FileRef, scan: Scan, mtime: float) -> str:
        if ref.cc_session in self.months:
            return self.months[ref.cc_session]
        ts = scan.first_ts
        if ts and len(ts) >= 7 and ts[4] == "-":
            month = ts[:7]
        else:
            month = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m")
        if ref.cc_session:
            self.months[ref.cc_session] = month
        return month

    def _git(self, cwd: Optional[str]) -> GitInfo:
        if not cwd:
            return GitInfo()
        if cwd not in self._git_cache:
            self._git_cache[cwd] = git_info(cwd, root=True)
        return self._git_cache[cwd]

    def _row(self, ref: FileRef, version: int, stored: str, source_path: str, scan: Scan,
             zst_bytes: int, origin: str, hint: Optional[dict] = None) -> dict:
        cwd = scan.fields.get("cwd")
        session_context = self.context.get(ref.cc_session or "", {})
        if not cwd:
            cwd = session_context.get("cwd")
        remote, branch, _, git_root = self._git(cwd)
        if cwd == session_context.get("cwd") and not (remote or git_root):
            remote = session_context.get("git_remote")
            git_root = session_context.get("git_root")
        identity = self._identity(ref.cc_session, hint)
        row = {
            "v": ROW_VERSION,
            "captured_at": utc_now(),
            "backend": BACKEND,
            "cc_session": ref.cc_session,
            "kind": ref.kind,
            "rel": self._rel(ref, stored),
            "stored": stored,
            "key": ref.key,
            "version": version,
            "source_path": source_path,
            "project": ref.project,
            "sha256": scan.sha256,
            "bytes": scan.bytes,
            "zst_bytes": zst_bytes,
            "first_ts": scan.first_ts,
            "last_ts": scan.last_ts,
            "cc_version": scan.fields.get("cc_version") or session_context.get("cc_version"),
            "cwd": cwd,
            "git_remote": remote,
            "git_branch": scan.fields.get("git_branch") or branch,
            "git_root": git_root,
            "agent": identity.get("agent"),
            "agent_id": identity.get("agent_id"),
            "billing_code": identity.get("billing_code"),
            "billing_code_id": identity.get("billing_code_id"),
            "parent_agent": identity.get("parent_agent"),
            "secrets_flagged": scan.secrets,
            "origin": origin,
        }
        if ref.kind == KIND_MAIN and ref.cc_session:
            self.context[ref.cc_session] = _context_of(row)
        return row

    def _identity(self, cc_session: Optional[str], hint: Optional[dict]) -> dict:
        """The agent of a conversation: the one whose hook queued it, else the records'."""
        if hint and hint.get("agent_id"):
            known = self.identities().of_agent(hint["agent_id"])
            if known:
                return dict(known)
            return {k: hint.get(k) for k in ("agent", "agent_id") if hint.get(k)}
        return dict(self.identities().lookup(cc_session))

    def _rel(self, ref: FileRef, stored: str) -> str:
        return stored.split("/", 1)[1] if ref.kind == KIND_HISTORY else stored.split("/", 2)[2]

    def _decide(self, ref: FileRef, source_path: str, size: int,
                prefix_sha: Optional[str], sha: str) -> Tuple[str, int]:
        """``(action, version)``: unchanged, duplicate (already archived under the
        key), append (replace the version the source was captured as) or new.

        An append never replaces a version another source also stands for
        (a set-aside copy equal to it): that one is kept and a new version added.
        """
        versions = self.versions.get(ref.key, {})
        previous = self.previous(source_path)
        if previous and previous.get("key") == ref.key:
            if previous["sha256"] == sha:
                return "unchanged", previous["version"]
            shared = (source_path in self.aliases
                      or previous["stored"] in self.aliases.values())
            if (not shared and size > previous["bytes"] and prefix_sha == previous["sha256"]
                    and versions.get(previous["version"], {}).get("sha256") == previous["sha256"]):
                return "append", previous["version"]
        for version, row in versions.items():
            if row.get("sha256") == sha:
                return "duplicate", version
        return "new", max(versions, default=0) + 1

    def over_cap(self) -> bool:
        return bool(self.max_bytes) and self.stored_bytes >= self.max_bytes

    def capture_file(self, path: str, ref: FileRef, *, origin: str, result: Result,
                     source_path: Optional[str] = None, hint: Optional[dict] = None,
                     st: Optional[os.stat_result] = None) -> str:
        """Archive a snapshot of ``path`` if its content is new.

        Returns what happened: ``captured``, ``unchanged`` (this content is
        archived already), ``retry`` (the file changed while it was read)
        or ``over_cap``. Only the first two mean the file is safe here.
        """
        source_path = source_path or display_path(path)
        st = st or os.stat(path)
        size = st.st_size
        previous = self.previous(source_path)
        prefix_at = previous["bytes"] if previous and previous.get("key") == ref.key else None
        if prefix_at is not None and prefix_at > size:
            prefix_at = None
        scan = scan_file(path, size, ref, prefix_at)
        if scan.bytes != size:
            result.skipped += 1  # shrank while being read: next sweep
            return "retry"
        action, version = self._decide(ref, source_path, size, scan.prefix_sha256, scan.sha256)
        if action in ("unchanged", "duplicate"):
            result.unchanged += 1
            if action == "duplicate":
                stored = self.versions[ref.key][version]["stored"]
                if self.latest_by_source.get(source_path, {}).get("stored") != stored:
                    self.aliases[source_path] = stored
            return "unchanged"
        if self.over_cap():
            result.over_cap += 1
            return "over_cap"
        month = self._month_for(ref, scan, st.st_mtime)
        stored = (previous["stored"] if action == "append" else stored_path(ref, version, month))
        dest = self.root / stored
        ensure_private_dir(dest.parent, self.root)
        compressor = _Compressor(dest, size, gentle=self.gentle)
        check = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in _read_exactly(f, size):
                    check.update(chunk)
                    compressor.write(chunk)
            if check.hexdigest() != scan.sha256:
                compressor.abort()
                result.skipped += 1  # rewritten while being read: next sweep
                return "retry"
            zst_bytes = compressor.commit()
        except BaseException:
            compressor.abort()
            raise
        row = self._row(ref, version, stored, source_path, scan, zst_bytes, origin, hint)
        old = self.by_stored.get(stored)
        self.stored_bytes += zst_bytes - (int(old.get("zst_bytes") or 0) if old else 0)
        self._append_rows([row])
        result.rows += 1
        result.captured += 1
        result.appended += action == "append"
        result.versioned += action == "new" and version > 1
        result.bytes += scan.bytes
        result.zst_bytes += zst_bytes
        result.secrets_flagged += scan.secrets
        return "captured"

    # ── the sweep ───────────────────────────────────────────────────

    def iter_sources(self) -> Iterator[Tuple[str, FileRef]]:
        """Every file under Claude's ``projects`` directory that has a place here."""
        projects = self.source_root / "projects"
        base = str(self.source_root)
        for directory, dirs, files in os.walk(projects):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(directory, name)
                ref = classify(os.path.relpath(path, base).replace(os.sep, "/"))
                if ref is not None:
                    yield path, ref

    def sweep(self, *, now: bool = False, result: Optional[Result] = None) -> Result:
        """Archive what changed under ``projects`` since the last sweep (caller holds the lock).

        ``now`` takes appends to files still being written, too.
        """
        started = time.monotonic()
        result = result or Result()
        result.modes_fixed += self.fix_modes()
        stats = self.state["stat"]
        wall = self.clock()
        seen = set()
        for path, ref in self.iter_sources():
            source_path = display_path(path)
            seen.add(source_path)
            result.seen += 1
            try:
                st = os.stat(path)
            except OSError:
                continue
            signature = [st.st_size, st.st_mtime_ns]
            if stats.get(source_path) == signature:
                result.unchanged += 1
                continue
            previous = self.previous(source_path)
            if (not now and previous and previous.get("key") == ref.key
                    and st.st_size > previous.get("bytes", 0)
                    and wall - st.st_mtime < QUIET_SECONDS
                    and wall - _epoch(previous.get("captured_at")) < MAX_DEFER_SECONDS):
                result.deferred += 1
                continue
            try:
                outcome = self.capture_file(path, ref, origin="sweep", result=result,
                                            source_path=source_path, st=st)
            except FileNotFoundError:
                continue
            except ArchiveError:
                raise
            except Exception as e:
                result.failed += 1
                logger.warning("transcript archive: %s not captured: %s",
                               source_path, type(e).__name__)
                continue
            if outcome in ("captured", "unchanged"):
                stats[source_path] = signature
        for gone in set(stats) - seen:
            del stats[gone]
        result.seconds = round(time.monotonic() - started, 3)
        self.state["last_sweep"] = {"at": utc_now(), **result.as_dict()}
        self.save_state()
        return result

    def drain_queue(self, result: Optional[Result] = None) -> Result:
        """Capture the files of each conversation the hooks queued (caller holds the lock)."""
        started = time.monotonic()
        result = result or Result()
        entries, draining = transcript_queue.take(self.root)
        base = os.path.realpath(self.source_root)
        done = set()
        for entry in entries:
            path = os.path.realpath(expand_path(entry["path"]))
            if path in done:
                continue
            done.add(path)
            if not path.startswith(base + os.sep):
                result.skipped += 1  # not a Claude transcript of this machine
                continue
            targets = [path]
            session_dir = path[:-len(".jsonl")] if path.endswith(".jsonl") else None
            if session_dir and os.path.isdir(session_dir):
                for directory, dirs, files in os.walk(session_dir):
                    dirs.sort()
                    targets.extend(os.path.join(directory, n) for n in sorted(files))
            for target in targets:
                rel = os.path.relpath(target, base).replace(os.sep, "/")
                ref = classify(rel)
                if ref is None:
                    result.skipped += 1
                    continue
                # Named as the sweep names it, whatever spelling the hook used
                source_path = display_path(str(self.source_root / rel))
                result.seen += 1
                try:
                    st = os.stat(target)
                    outcome = self.capture_file(target, ref, origin="hook", result=result,
                                                source_path=source_path, hint=entry, st=st)
                    if outcome in ("captured", "unchanged"):
                        self.state["stat"][source_path] = [st.st_size, st.st_mtime_ns]
                except FileNotFoundError:
                    continue
                except ArchiveError:
                    raise
                except Exception as e:
                    result.failed += 1
                    logger.warning("transcript archive: queued file not captured: %s",
                                   type(e).__name__)
        try:
            os.unlink(draining)
        except FileNotFoundError:
            pass
        result.seconds = round(time.monotonic() - started, 3)
        if entries:
            self.state["last_drain"] = {"at": utc_now(), "entries": len(entries),
                                        **result.as_dict()}
            self.save_state()
        return result

    # ── import ──────────────────────────────────────────────────────

    def import_snapshot(self, snapshot: Path, result: Optional[Result] = None) -> Result:
        """Take every file of a ``tar.zst`` of Claude's directory (caller holds the lock).

        Member names are paths under Claude's directory (``projects/...``,
        ``file-history/...``, ``history.jsonl``), as ``tar -C ~/.claude``
        writes them. macOS tar's AppleDouble entries are not files of the
        snapshot and are left out; a hard link is archived as its target's
        content. Importing the same snapshot again adds nothing.
        """
        started = time.monotonic()
        result = result or Result()
        snapshot = Path(snapshot)
        origin = f"import:{snapshot.name}"
        proc = subprocess.Popen([_need_zstd(), "-dc", "-q", "--long=31", str(snapshot)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        by_name: Dict[str, Tuple[FileRef, str]] = {}
        scratch = self.root / f".import-{os.getpid()}"
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for member in tar:
                    name = member.name.removeprefix("./")
                    if not (member.isfile() or member.islnk()):
                        continue
                    temp = scratch / "member"
                    if member.isfile():
                        ensure_private_dir(scratch, self.root)
                        handle = tar.extractfile(member)
                        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        with os.fdopen(fd, "wb") as out:
                            shutil.copyfileobj(handle, out, CHUNK)
                        if os.path.basename(name).startswith("._"):
                            with open(temp, "rb") as f:
                                if f.read(4) == _APPLEDOUBLE_MAGIC:
                                    continue
                    ref = classify(name)
                    if ref is None:
                        result.skipped += 1
                        logger.warning("transcript archive: snapshot member not placed")
                        continue
                    result.seen += 1
                    source_path = display_path(str(self.source_root / name))
                    if member.islnk():
                        target = member.linkname.removeprefix("./")
                        if target not in by_name:
                            result.failed += 1
                            continue
                        self._import_link(ref, source_path, *by_name[target], member, origin, result)
                        by_name[name] = (ref, source_path)
                        continue
                    os.utime(temp, (member.mtime, member.mtime))
                    outcome = self.capture_file(str(temp), ref, origin=origin, result=result,
                                                source_path=source_path)
                    if outcome not in ("captured", "unchanged"):
                        result.failed += 1
                    by_name[name] = (ref, source_path)
        except tarfile.TarError as e:
            raise ArchiveError(f"not a readable tar.zst snapshot: {e}") from None
        finally:
            proc.stdout.close()
            code = proc.wait()
            shutil.rmtree(scratch, ignore_errors=True)
        if code != 0:
            raise ArchiveError("zstd could not read the snapshot")
        result.seconds = round(time.monotonic() - started, 3)
        self.state.setdefault("imports", {})[snapshot.name] = {"at": utc_now(), **result.as_dict()}
        self.save_state()
        return result

    def _import_link(self, ref: FileRef, source_path: str, target_ref: FileRef,
                     target_source: str, member, origin: str, result: Result) -> None:
        """A hard link of the snapshot: its target's content, under its own name."""
        target = self.previous(target_source)
        if target is None:
            result.failed += 1
            return
        sha = target["sha256"]
        action, version = self._decide(ref, source_path, target["bytes"], None, sha)
        if action in ("unchanged", "duplicate"):
            result.unchanged += 1
            if action == "duplicate":
                self.aliases[source_path] = self.versions[ref.key][version]["stored"]
            return
        if self.over_cap():
            result.over_cap += 1
            result.failed += 1
            return
        scan = Scan(sha256=sha, bytes=target["bytes"], secrets=target.get("secrets_flagged", 0),
                    first_ts=target.get("first_ts"), last_ts=target.get("last_ts"))
        month = self._month_for(ref, scan, member.mtime)
        stored = stored_path(ref, version, month)
        dest = self.root / stored
        ensure_private_dir(dest.parent, self.root)
        _write_private(dest, (self.root / target["stored"]).read_bytes())
        row = self._row(ref, version, stored, source_path, scan, target["zst_bytes"], origin)
        self.stored_bytes += target["zst_bytes"]
        self._append_rows([row])
        result.rows += 1
        result.captured += 1
        result.bytes += scan.bytes
        result.zst_bytes += target["zst_bytes"]
        result.secrets_flagged += scan.secrets

    # ── reading ─────────────────────────────────────────────────────

    def current_rows(self) -> List[dict]:
        """The newest row of each stored file, in manifest order."""
        latest: Dict[str, dict] = {}
        for row in self.rows():
            path = row.get("stored")
            if isinstance(path, str):
                latest.pop(path, None)
                latest[path] = row
        return list(latest.values())

    def status(self) -> dict:
        """The archive at a glance: counts and sizes, never content."""
        rows = self.current_rows()
        sessions = {r["cc_session"] for r in rows if r.get("cc_session")}
        flagged = [r for r in rows if r.get("secrets_flagged")]
        state = self.state if self.state else _read_json(self.root / STATE_FILE)
        stats = (state or {}).get("stat") or {}
        pending = 0
        if self.source_root.joinpath("projects").is_dir():
            for path, _ref in self.iter_sources():
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if stats.get(display_path(path)) != [st.st_size, st.st_mtime_ns]:
                    pending += 1
        total = sum(int(r.get("zst_bytes") or 0) for r in rows)
        return {
            "root": display_path(str(self.root)),
            "sessions": len(sessions),
            "files": len(rows),
            "manifest_rows": sum(1 for _ in self.rows()),
            "bytes": sum(int(r.get("bytes") or 0) for r in rows),
            "zst_bytes": total,
            "max_bytes": self.max_bytes or None,
            "cap_used": round(total / self.max_bytes, 3) if self.max_bytes else None,
            "secrets_flagged_files": len(flagged),
            "secrets_flagged": sum(int(r["secrets_flagged"]) for r in flagged),
            "last_sweep": (state or {}).get("last_sweep"),
            "last_drain": (state or {}).get("last_drain"),
            "imports": (state or {}).get("imports"),
            "queued": transcript_queue.pending(self.root),
            "pending": pending,
            "zstd": bool(zstd_binary()),
        }

    def find(self, *, repo: Optional[str] = None, since: Optional[str] = None,
             agent: Optional[str] = None, billing: Optional[str] = None,
             cc_session: Optional[str] = None, kinds: Optional[Iterable[str]] = None) -> List[dict]:
        """The current rows that match every filter given (see :func:`match_repo`)."""
        rows = self.current_rows()
        kinds = set(kinds) if kinds else None
        if repo:
            rows = match_repo(rows, repo)
        out = []
        for row in rows:
            if kinds and row.get("kind") not in kinds:
                continue
            if cc_session and row.get("cc_session") != cc_session:
                continue
            if agent and agent not in (row.get("agent"), row.get("agent_id")):
                continue
            if billing and billing not in (row.get("billing_code"), row.get("billing_code_id")):
                continue
            if since and (row.get("last_ts") or row.get("captured_at") or "") < since:
                continue
            out.append(row)
        return out

    def session_files(self, cc_session: str, *, subagents: bool = False,
                      version: Optional[int] = None) -> List[dict]:
        """The rows ``cat`` prints for a conversation: its main transcript, then its subagents'.

        The main transcript is the version captured from the live file (not
        a set-aside copy), else the highest; ``version`` picks one.
        """
        rows = [r for r in self.current_rows() if r.get("cc_session") == cc_session]
        mains = [r for r in rows if r.get("kind") == KIND_MAIN]
        chosen: List[dict] = []
        if version is not None:
            chosen = [r for r in mains if r.get("version") == version]
        elif mains:
            live = [r for r in mains if not _SET_ASIDE_RE.search(r.get("source_path") or "")]
            chosen = [max(live or mains, key=lambda r: (r.get("version", 0), r.get("bytes", 0)))]
        if subagents:
            best: Dict[str, dict] = {}
            for row in rows:
                if row.get("kind") == KIND_SUBAGENT and row.get("key", "").endswith(".jsonl"):
                    current = best.get(row["key"])
                    if current is None or row.get("version", 0) > current.get("version", 0):
                        best[row["key"]] = row
            chosen.extend(sorted(best.values(), key=lambda r: r["stored"]))
        return chosen

    def cat(self, cc_session: str, out: BinaryIO, *, subagents: bool = False,
            version: Optional[int] = None) -> int:
        """Write the conversation's transcript(s) to ``out``; how many files."""
        files = self.session_files(cc_session, subagents=subagents, version=version)
        for row in files:
            decompress_to(self.root / row["stored"], out)
        return len(files)


def _context_of(row: dict) -> dict:
    return {k: row.get(k) for k in ("cwd", "git_remote", "git_branch", "git_root", "cc_version")}


def _epoch(when: Optional[str]) -> float:
    if not when:
        return 0.0
    try:
        return datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_manifest(path: Path) -> Iterator[dict]:
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return
    with f:
        for line in f:
            if not line.endswith(b"\n"):
                break  # a write in progress
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def match_repo(rows: List[dict], repo: str) -> List[dict]:
    """The rows of repository ``repo``: a remote (any spelling) or a local path.

    A row with a remote counts when it is that remote. A row without one
    (no remote, or its ``cwd`` was gone when it was captured) counts when
    its root commit is the repository's (a clone with no remote; another
    repository nested in a checkout has its own), else when its ``cwd`` is
    inside a checkout of the repository (the path given, or one other rows
    were captured in: a removed worktree), or is gone and was a
    ``<checkout>-<anything>`` copy beside one (``~/Code/shirka-easy`` for
    ``~/Code/shirka``). That last is a guess from the name: a removed
    ``~/Code/shirka-notes`` that was never shirka counts too.
    """
    roots: List[str] = []
    commits = set()
    wanted: Optional[str] = None
    candidate = os.path.expanduser(repo)
    if os.path.isdir(candidate):
        real = os.path.realpath(candidate)
        roots.append(real)
        info = git_info(real, root=True)
        wanted = info.remote
        if info.top:
            roots.append(os.path.realpath(info.top))
        if info.root:
            commits.add(info.root)
    else:
        wanted = normalize_remote(repo)
    if wanted:
        for row in rows:
            if same_remote(row.get("git_remote"), wanted):
                if row.get("cwd"):
                    roots.append(_checkout_root(row["cwd"]))
                if row.get("git_root"):
                    commits.add(row["git_root"])
    roots = sorted(set(r for r in roots if r and r != os.sep))
    siblings = [r + "-" for r in roots]

    def inside(cwd: str) -> bool:
        return any(cwd == r or cwd.startswith(r + os.sep) for r in roots)

    out = []
    for row in rows:
        remote = row.get("git_remote")
        if wanted and same_remote(remote, wanted):
            out.append(row)
            continue
        if remote or not row.get("cwd"):
            continue
        cwd, git_root = _real(row["cwd"]), row.get("git_root")
        if git_root and commits:
            found = git_root in commits  # another repository nested in a checkout is not it
        else:
            found = inside(cwd) or (not git_root and _gone_copy_beside(cwd, siblings))
        if found:
            out.append(row)
    return out


def _real(path: str) -> str:
    """``path`` with symlinks resolved as far as it still exists (``/tmp`` is ``/private/tmp``)."""
    return os.path.realpath(path)


def _gone_copy_beside(cwd: str, siblings: List[str]) -> bool:
    """Whether ``cwd`` was in a removed ``<checkout>-<suffix>`` directory beside a checkout."""
    for prefix in siblings:
        if cwd.startswith(prefix) and len(cwd) > len(prefix):
            copy = prefix + cwd[len(prefix):].split(os.sep, 1)[0]
            return not os.path.exists(copy)
    return False


@functools.lru_cache(maxsize=4096)
def _checkout_root(cwd: str) -> str:
    """The worktree root holding ``cwd`` (``cwd`` itself when git cannot say)."""
    top = git_info(cwd).top
    return os.path.realpath(top) if top else _real(cwd)


def _describe(error: BaseException) -> str:
    """An exception for the daemon's log: its type, and its text only when that
    is ours or the OS's (paths and codes). Other text could quote what was read."""
    if isinstance(error, (ArchiveError, OSError)):
        return f"{type(error).__name__}: {error}"
    return type(error).__name__


# ── the daemon's part ────────────────────────────────────────────────────

class ArchiveScheduler:
    """Runs the sweep hourly and drains the hook queue, on a background thread.

    ``tick`` is cheap (a monotonic comparison and a ``stat``) and never
    raises: the monitor daemon calls it every loop. Every monitor daemon
    on the machine has one; the archive's lock and the last sweep's time
    in ``state.json`` keep it to one sweep an hour between them.
    """

    def __init__(self, log=None, *, archive_factory: Optional[Callable[[], TranscriptArchive]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.log = log
        self.clock = clock
        self._factory = archive_factory or self._default_archive
        self.thread: Optional[threading.Thread] = None
        self.last_queue_check: Optional[float] = None
        self.last_sweep_check: Optional[float] = None
        self._warned_cap = False

    @staticmethod
    def _default_archive() -> TranscriptArchive:
        from .config import get_transcript_archive_config

        cfg = get_transcript_archive_config()
        return TranscriptArchive(max_bytes=cfg["max_bytes"], gentle=True)

    @staticmethod
    def enabled() -> bool:
        from .config import get_transcript_archive_config

        return get_transcript_archive_config()["sweep"]

    def tick(self) -> None:
        try:
            self._tick()
        except Exception as e:  # never fail the daemon's loop
            self._warn(f"transcript archive: {_describe(e)}")

    def _tick(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        mono = self.clock()
        sweep_due = self.last_sweep_check is None or mono - self.last_sweep_check >= SWEEP_INTERVAL_SECONDS
        queue_due = self.last_queue_check is None or mono - self.last_queue_check >= QUEUE_CHECK_SECONDS
        if not (sweep_due or queue_due):
            return
        if not self.enabled():
            self.last_sweep_check = self.last_queue_check = mono
            return
        root = transcripts_dir()
        queued = queue_due and transcript_queue.pending(root) > 0
        if queue_due:
            self.last_queue_check = mono
        if sweep_due:
            self.last_sweep_check = mono
            last = (_read_json(root / STATE_FILE) or {}).get("last_sweep") or {}
            if time.time() - _epoch(last.get("at")) < SWEEP_INTERVAL_SECONDS * 0.9:
                sweep_due = False  # another daemon swept within the hour
        if not (sweep_due or queued):
            return
        self.thread = threading.Thread(target=self._run, args=(sweep_due, queued),
                                       name="transcript-archive", daemon=True)
        self.thread.start()

    def _run(self, sweep: bool, drain: bool) -> None:
        try:
            archive = self._factory()
            with archive.locked(wait=False):
                if drain:
                    result = archive.drain_queue()
                    if result.captured:
                        self._info(f"transcript archive: {result.captured} queued file(s) captured")
                if sweep:
                    result = archive.sweep()
                    if result.captured or result.failed:
                        self._info(
                            f"transcript archive sweep: {result.captured} captured "
                            f"({result.appended} appended, {result.versioned} new versions), "
                            f"{result.deferred} deferred, {result.failed} failed, "
                            f"{result.seconds:.0f}s")
                self._check_cap(archive)
        except ArchiveBusy:
            pass
        except Exception as e:
            self._warn(f"transcript archive: {_describe(e)}")

    def _check_cap(self, archive: TranscriptArchive) -> None:
        if not archive.max_bytes:
            return
        used = archive.stored_bytes / archive.max_bytes
        if used >= ALERT_FRACTION and not self._warned_cap:
            self._warned_cap = True
            self._warn(f"transcript archive at {used:.0%} of archive.transcripts.max_bytes"
                       + ("; new copies are not being taken" if used >= 1 else ""))
        elif used < ALERT_FRACTION:
            self._warned_cap = False

    def _info(self, message: str) -> None:
        if self.log is not None:
            self.log.info(message)

    def _warn(self, message: str) -> None:
        if self.log is not None:
            self.log.warn(message)
        else:
            logger.warning(message)
