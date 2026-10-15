"""Moving a tmux session's storage to the 0.6 layout, in batches.

Before 0.6 each agent's files sat directly in ``sessions/<tmux>/``, named
after the agent (``hook_state_<name>.json``, ``report_<name>.json``, ...),
and they were never removed: a heavy user had tens of thousands, and every
listing of the directory paid for them. Now an agent's files live in
``agents/<session id>/`` while it is live and in a day bundle after
(``agent_bundle``), the archive is a slim file per month and spend has a
ledger.

``migrate_layout`` gets a tmux session there, a bounded batch at a time,
from the monitor daemon (at start, then with the hourly storage tidy):

1. the single-file archive is split into slim months (``session_archive``);
2. the ledger is backfilled from the archive (billing code: the agent's
   own, else its parent's up to the root, else its name), again whenever
   an older overcode's archive file is taken in;
3. live records with no billing code get their parent's code, up to the
   root, else their name (sticky from now on). This one runs on every
   call, done or not, and the daemon repeats it each housekeeping pass
   (``Housekeeping.fill_billing_codes``): an older overcode still running
   (or a downgrade) can launch a child after the migration, and nothing
   else would ever give it its parent's code;
4. name-keyed files left in the session directory are packed into today's
   bundle (one member per name, ``"legacy": true``) and removed — except
   those of a live agent launched before the upgrade (no ``agents/<id>/``,
   its hooks or plugin still write them), which are left until it is
   relaunched.

When nothing is left to move, ``sessions/<tmux>/.layout`` says ``2`` and
the next call costs two reads (the marker, the live records). Every step is idempotent: a crash part-way
repeats a batch (a duplicate bundle member), never loses a file.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .paths import AGENT_FILES, agent_dir, bundle_path, session_dir

logger = logging.getLogger(__name__)

LAYOUT_VERSION = "2"
# Names (each with up to six files) moved per call
BATCH_NAMES = 200

# (prefix, suffix) of each name-keyed file, plus the lock and temp files
# beside them; the name is what is between.
_PATTERNS = [tuple(pattern.split("{}")) for pattern in AGENT_FILES.values()]
_EXTRA_SUFFIXES = (".lock", ".tmp", ".1")


def layout_marker(tmux_session: str) -> Path:
    return session_dir(tmux_session) / ".layout"


def layout_version(tmux_session: str) -> str:
    """The session directory's layout: ``2`` once migrated, else ``1``."""
    try:
        return layout_marker(tmux_session).read_text().strip() or "1"
    except OSError:
        return "1"


def legacy_name(filename: str) -> Optional[tuple]:
    """``(name, kind)`` of a pre-0.6 name-keyed agent file, else None."""
    base = filename
    for extra in _EXTRA_SUFFIXES:
        if base.endswith(extra) and not any(base.endswith(s) for _, s in _PATTERNS):
            base = base[: -len(extra)]
    for (prefix, suffix), kind in zip(_PATTERNS, AGENT_FILES):
        if base.startswith(prefix) and base.endswith(suffix) and len(base) > len(prefix) + len(suffix):
            return base[len(prefix): len(base) - len(suffix)], kind
    return None


def legacy_files(tmux_session: str) -> Dict[str, List[Path]]:
    """The name-keyed files in a session directory, by agent name (one scandir)."""
    found: Dict[str, List[Path]] = {}
    try:
        entries = list(os.scandir(session_dir(tmux_session)))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_file():
            continue
        parsed = legacy_name(entry.name)
        if parsed is not None:
            found.setdefault(parsed[0], []).append(Path(entry.path))
    return found


def _pack_legacy(tmux_session: str, name: str, files: List[Path], now: datetime) -> None:
    """One bundle member holding a name's leftover files (what they were is kept)."""
    import gzip
    import json

    from .agent_bundle import _append_member, _tail_text, OTHER_TAIL_BYTES, TEXT_FILES

    kept: Dict[str, object] = {}
    for path in files:
        if path.name.endswith((".lock", ".tmp")):
            continue
        limit = max(TEXT_FILES.values()) if path.suffix in (".jsonl", ".1") else OTHER_TAIL_BYTES
        text = _tail_text(path, limit)
        if text is None:
            continue
        if path.suffix == ".json":
            try:
                kept[path.name] = json.loads(text)
                continue
            except ValueError:
                pass
        kept[path.name] = text
    if not kept:
        return
    record = {"name": name, "tmux": tmux_session, "legacy": True,
              "archived_at": now.isoformat(), "files": kept}
    member = gzip.compress((json.dumps(record, default=str) + "\n").encode("utf-8"), mtime=0)
    _append_member(bundle_path(tmux_session, now.strftime("%Y-%m-%d")), member)


def pack_name(tmux_session: str, name: str, now: Optional[datetime] = None) -> bool:
    """Pack one name's leftover files into today's bundle, then remove them.

    For a rename of an agent launched before the upgrade: nobody is called
    ``name`` any more, so its files must go, but packed as ``migrate_layout``
    would have packed them, never just deleted. False (files left for the
    migration) when packing fails.
    """
    files = legacy_files(tmux_session).get(name)
    if not files:
        return True
    try:
        _pack_legacy(tmux_session, name, files, now or datetime.now())
    except Exception as e:
        logger.warning("could not pack %s's files: %s", name, e)
        return False
    for path in files:
        try:
            path.unlink()
        except OSError:
            pass
    return True


def migrate_layout(tmux_session: str, session_manager, *, batch: int = BATCH_NAMES,
                   now: Optional[datetime] = None) -> Dict[str, int]:
    """Move one batch of a tmux session's storage to the 0.6 layout (see module docstring).

    Returns counts: ``packed`` names, ``skipped`` (a live agent's), ``left``
    (for a later call), ``backfilled`` ledger rows, ``coded`` records, and
    ``done`` (1 once the marker is written). Never raises.
    """
    counts = {"packed": 0, "skipped": 0, "left": 0, "backfilled": 0, "coded": 0, "done": 0}
    if (layout_version(tmux_session) == LAYOUT_VERSION
            and session_manager.ledger.backfilled()):
        counts["done"] = 1
        try:
            counts["coded"] = len(fill_billing_codes(session_manager))
        except Exception as e:
            logger.warning("billing codes not filled: %s", e)
        return counts
    now = now or datetime.now()
    try:
        session_manager._migrate_legacy_archive()
        counts["backfilled"] = session_manager.ledger.backfill_from_archive(session_manager)
        counts["coded"] = len(fill_billing_codes(session_manager))
    except Exception as e:
        logger.warning("storage migration (archive, ledger) failed: %s", e)
        return counts

    live = {}
    for session in session_manager.list_sessions():
        if session.tmux_session == tmux_session:
            live[session.name] = session
    pending = legacy_files(tmux_session)
    for name in sorted(pending):
        files = pending[name]
        session = live.get(name)
        if session is not None and not agent_dir(tmux_session, session.id).is_dir():
            counts["skipped"] += 1  # launched before: its hooks still write these
            continue
        if counts["packed"] >= batch:
            counts["left"] += 1
            continue
        try:
            _pack_legacy(tmux_session, name, files, now)
        except Exception as e:
            logger.warning("could not pack %s's files: %s", name, e)
            counts["left"] += 1
            continue
        for path in files:
            try:
                path.unlink()
            except OSError:
                pass
        counts["packed"] += 1
    if not counts["skipped"] and not counts["left"]:
        try:
            marker = layout_marker(tmux_session)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(LAYOUT_VERSION + "\n")
            counts["done"] = 1
        except OSError:
            pass
    return counts


def fill_billing_codes(session_manager, sessions=None) -> Dict[str, str]:
    """Give each live record with no billing code one (one write, if any).

    Its parent's code, walking up to the root through live and archived
    records (children always bill to the parent), else its name. Returns
    the codes of those found with none, by session id (one a stale
    ``sessions`` lacks but the file has is returned, not rewritten).
    ``sessions`` (the live records, when the caller has them) spares a
    read when none is missing.
    """
    from .ledger import billing_lookup, inherited_billing_code

    if sessions is None:
        sessions = session_manager.list_sessions()
    missing = [s.id for s in sessions if not s.billing_code]
    if not missing:
        return {}
    archived = billing_lookup(session_manager.iter_archived_records(newest_first=True))
    filled: Dict[str, str] = {}
    with session_manager._state_transaction() as txn:
        lookup = {**archived, **billing_lookup(txn.state.values())}
        for session_id in missing:
            entry = txn.state.get(session_id)
            if entry is None or not entry.get("name"):
                continue
            if not entry.get("billing_code"):
                entry["billing_code"] = inherited_billing_code(entry, lookup)
                txn.dirty = True
            filled[session_id] = entry["billing_code"]
    return filled
