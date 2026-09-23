"""Stat-signature gate for the shared JSON state files (audit R4).

``sessions.json`` and ``monitor_daemon_state.json`` are read by every TUI
worker and by the daemon tick — five or six full ``json.load`` passes a
second per process — while the files only change when something writes
them. A parse costs ~4.7 ms per MB (80 ms for 2,000 session entries), so
the parse rate has to follow the *write* rate, not the read rate.

The gate is one ``os.stat`` per read: while ``(st_mtime_ns, st_size,
st_ino)`` matches the last parse, the previous result is returned — no
lock, no read, no ``json.loads``, no object construction. Every writer
replaces the whole file, by ``rename`` (new inode) or by truncate+dump under
``LOCK_EX`` (same inode, new ``mtime_ns``), so any write moves the
signature; a same-size in-place rewrite still moves ``mtime_ns``.

The one hole is timestamp granularity. A filesystem stamps ``mtime`` from a
clock that may tick coarser than nanoseconds — jiffies (1–10 ms) on Linux
before 6.13's multigrain timestamps — so two same-size writes inside one
tick share a signature, and a reader that parsed the first would keep it
until the third write. The cache therefore only *trusts* a signature once
its ``mtime`` is older than :data:`SETTLE_NS` at the moment the bytes were
parsed: after that, the writer's clock has moved past the stamp and no
later write can reproduce it. A parse of a just-written file is returned
but not remembered, which costs at most one extra parse per write and keeps
"write, then read" exact even for back-to-back writes. Filesystems with
whole-second stamps (HFS+, ext3) are outside that guarantee: a same-size
rewrite inside the same second as a trusted parse is missed until the next
write — for ``sessions.json`` that is the daemon's next tick.
"""

import os
import threading
import time
from typing import Callable, Generic, NamedTuple, Optional, Tuple, TypeVar

T = TypeVar("T")

# How much older than "now" a file's mtime must be before its signature is
# trusted to identify the content uniquely. Linux's coarse clock (HZ=100)
# ticks every 10 ms and can lag the fine clock by one tick; 50 ms leaves a
# 2.5x margin over that worst case.
SETTLE_NS = 50_000_000


class FileSignature(NamedTuple):
    """What identifies one version of a rewritten file, from a single stat."""

    mtime_ns: int
    size: int
    ino: int

    @classmethod
    def of(cls, st: os.stat_result) -> "FileSignature":
        return cls(st.st_mtime_ns, st.st_size, st.st_ino)


def stat_signature(path) -> Optional[FileSignature]:
    """Signature of the file at ``path`` now, or ``None`` if it cannot be stat'ed."""
    try:
        return FileSignature.of(os.stat(path))
    except OSError:
        return None


def is_settled(sig: FileSignature, now_ns: Optional[int] = None) -> bool:
    """True once no future write can stamp the same ``mtime_ns`` as ``sig``.

    Compares the stamp against the same realtime clock the kernel writes
    it from. A clock stepped backwards makes every fresh parse look
    unsettled, so the cache degrades to today's parse-per-read until the
    clock passes the stamp again — never to a stale result.
    """
    if now_ns is None:
        now_ns = time.time_ns()
    return now_ns - sig.mtime_ns > SETTLE_NS


class StatGatedCache(Generic[T]):
    """The last parse of one file, reused while the file's signature is unchanged.

    ``get`` stats the file and, on a signature match, returns the remembered
    value. Otherwise it calls ``read`` under the cache lock (concurrent
    readers wait for one parse instead of each doing their own) and expects
    ``(signature, value)`` back, where ``signature`` is the ``fstat`` of the
    very bytes ``read`` parsed — taken under whatever file lock the reader
    holds, so an in-place writer cannot slip between the two — or ``None``
    when the file was missing or unreadable, in which case the value is
    returned but not remembered.

    Aliasing contract: every caller receives the *same* object until the
    file changes. Treat it as a read-only snapshot; persist changes through
    the writer API (which rewrites the file and thereby moves the
    signature) rather than by assigning to the returned object.
    """

    __slots__ = ("_sig", "_value", "_lock")

    def __init__(self) -> None:
        self._sig: Optional[FileSignature] = None
        self._value: Optional[T] = None
        self._lock = threading.Lock()

    def get(self, path, read: Callable[[], Tuple[Optional[FileSignature], T]]) -> T:
        sig = stat_signature(path)
        with self._lock:
            if sig is not None and sig == self._sig:
                return self._value  # type: ignore[return-value]
            read_sig, value = read()
            if read_sig is not None and is_settled(read_sig):
                self._sig, self._value = read_sig, value
            else:
                self._sig, self._value = None, None
            return value

    def invalidate(self) -> None:
        """Forget the remembered parse; the next ``get`` reads the file."""
        with self._lock:
            self._sig, self._value = None, None

    @property
    def signature(self) -> Optional[FileSignature]:
        """The trusted signature, or ``None`` when nothing is remembered."""
        return self._sig
