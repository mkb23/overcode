"""Month files shared by the ledger and the session archive.

Both keep one JSONL file per month, ``<YYYY-MM>.jsonl``, gzipped to
``<YYYY-MM>.jsonl.gz`` once the month is closed, and both are kept for
ever. The rules here keep that safe:

- a writer never adds to a closed month: ``writable_month`` (called under
  the directory's lock) moves a late write (a stale clock, a lock held
  across midnight) to the newest open month; readers order by the rows'
  own times, not by file;
- a month is gzipped, or a stray plain file folded into its gzip, by
  writing a whole new ``.gz.tmp``, fsyncing it, renaming it into place and
  fsyncing the directory before the plain file goes: a crash leaves the
  old file or the new one, never a torn one, and never neither;
- ``read_gzip`` reads every whole member of a gzip and what it can of a
  torn last one (a file written by an older in-place append), instead of
  losing the month to one bad member.
"""

from __future__ import annotations

import gzip
import os
import time
import zlib
from pathlib import Path
from typing import List, Tuple

PLAIN_SUFFIX = ".jsonl"
GZIP_SUFFIX = ".jsonl.gz"
_CHUNK = 1 << 20


def next_month(month: str) -> str:
    """The ``YYYY-MM`` after ``month``."""
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year + mon // 12:04d}-{mon % 12 + 1:02d}"


def month_files(directory: Path) -> Tuple[set, set]:
    """(months with a plain file, months with a gzip) in ``directory``."""
    plain, gzipped = set(), set()
    try:
        names = os.listdir(directory)
    except OSError:
        return plain, gzipped
    for name in names:
        if name.endswith(GZIP_SUFFIX):
            gzipped.add(name[: -len(GZIP_SUFFIX)])
        elif name.endswith(PLAIN_SUFFIX):
            plain.add(name[: -len(PLAIN_SUFFIX)])
    return plain, gzipped


def writable_month(directory: Path, month: str) -> str:
    """The month a write meant for ``month`` goes to (caller holds the lock).

    ``month`` itself, unless a later month already has a file (the clock
    is behind another writer's) or ``month`` is closed (gzipped): then the
    newest month that is still open.
    """
    plain, gzipped = month_files(directory)
    latest = max([month, *plain, *gzipped])
    while latest in gzipped:
        latest = next_month(latest)
    return latest


def fsync_dir(directory: Path) -> None:
    """Make renames and unlinks in ``directory`` durable (best effort)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def decompress(raw: bytes) -> Tuple[bytes, bool]:
    """The content of gzip bytes and whether every member was whole.

    A torn or damaged member ends the read: what came before it (and what
    could be inflated of it) is returned, with ``False``.
    """
    out: List[bytes] = []
    pos, size = 0, len(raw)
    while pos < size:
        if not raw[pos:].strip(b"\x00"):
            break  # trailing padding
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            while not inflater.eof and pos < size:
                chunk = raw[pos:pos + _CHUNK]
                pos += len(chunk)
                out.append(inflater.decompress(chunk))
        except zlib.error:
            return b"".join(out), False
        if not inflater.eof:
            out.append(inflater.flush())
            return b"".join(out), False
        pos -= len(inflater.unused_data)
    return b"".join(out), True


def read_gzip(path: Path) -> bytes:
    """A gzipped month's content, as much as can be read (see ``decompress``)."""
    return decompress(Path(path).read_bytes())[0]


def fold_into_gzip(plain: Path, target: Path) -> None:
    """Move ``plain``'s lines into the gzip ``target``, atomically; then remove ``plain``.

    The new gzip is the old one's bytes (when every member is whole) plus
    one member holding ``plain``, written to ``.gz.tmp``, fsynced and
    renamed into place. A damaged old gzip is rewritten from what can be
    read of it and itself kept beside it as ``.gz.damaged-<ns>``, so no byte is
    dropped. Raises OSError, leaving ``plain`` in place.
    """
    tmp = target.with_name(target.name + ".tmp")
    damaged = False
    with open(tmp, "wb") as out:
        if target.exists():
            raw = target.read_bytes()
            content, whole = decompress(raw)
            if whole:
                out.write(raw)
            else:
                damaged = True
                out.write(gzip.compress(content, mtime=0))
        with open(plain, "rb") as src, gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as dst:
            while chunk := src.read(_CHUNK):
                dst.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if damaged:
        os.replace(target, target.with_name(f"{target.name}.damaged-{time.time_ns()}"))
    os.replace(tmp, target)
    fsync_dir(target.parent)
    plain.unlink()
    fsync_dir(target.parent)
