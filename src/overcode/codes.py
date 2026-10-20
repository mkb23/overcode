"""Billing codes as entities: an id that never changes, a name that can.

``~/.overcode/ledger/codes.json`` (one entry per code, not per agent)::

    {"v": 1,
     "codes": {"c_<uuid4>": {"name", "source", "created", "previous_names",
                             "legacy", "roots", "merged_into"}},
     "legacy": {"<string>": "c_<uuid4>"}}

- The **id** (``c_`` + uuid4) is what sessions (``Session.billing_code_id``)
  and ledger rows (``code``) carry. The **name** is unique among codes; it
  is what people type and see. A rename changes only this file: every
  report, past and future, shows the new name. The old one goes in
  ``previous_names`` and still resolves.
- ``source`` is ``default`` (created implicitly for a launch, named after
  it) or ``human`` (given with ``--billing-code``, a rename, the TUI
  dialog). Only a ``default`` code used by a single agent tree (``roots``,
  the root agents that resolved to it, at most two kept) may follow its
  agent's name (``follow_agent_name``).
- **Legacy strings**: rows and records from before ids carry only a
  ``billing_code`` string. A one-time migration mints a code per distinct
  string (``adopt``) and maps it in ``legacy``; readers map id-less rows
  through it. A string stays mapped to its code after a rename, so a new
  code given the old name later gets a fresh id and none of the old spend.
- ``merged_into``: ``merge(A, B)`` folds A into B without touching a row
  (``merge_many``: several codes, one write).
  A's rows report under B, and A's names resolve to B. ``unmerge(A)``
  undoes it (``merge_added``: the names the merge gave B). ``collapse``
  merges codes into ``billing.default_code``.

A **code key** is what a row or record is grouped by before this registry
is consulted: its id, else its legacy string (``ledger.billing_code_of``).
``canonical`` turns a key into the code it reports under.

Written atomically under its own lock (``.codes.lock``), taken inside the
sessions lock by ``rebill`` and ``create_session``, never the other way.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
FILE_NAME = "codes.json"
ID_PREFIX = "c_"
DEFAULT, HUMAN = "default", "human"
# Roots kept per code: enough to know whether more than one tree used it
MAX_ROOTS = 2


class CodeError(ValueError):
    """A rename or merge the registry refuses (unknown code, name taken)."""


def is_code_id(key) -> bool:
    return isinstance(key, str) and key.startswith(ID_PREFIX) and len(key) == len(ID_PREFIX) + 32


def new_id() -> str:
    return ID_PREFIX + uuid.uuid4().hex


def _empty() -> dict:
    return {"v": REGISTRY_VERSION, "codes": {}, "legacy": {}}


class CodeRegistry:
    """The billing codes of one ledger directory (see the module docstring)."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.path = self.dir / FILE_NAME
        self._cache: Optional[Tuple[object, dict]] = None

    # ── files ───────────────────────────────────────────────────────────

    @contextmanager
    def _lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        if not HAS_FCNTL:
            yield
            return
        fd = os.open(self.dir / ".codes.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _signature(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    @staticmethod
    def _parse(text: str) -> dict:
        try:
            data = json.loads(text)
        except ValueError:
            return _empty()
        if not isinstance(data, dict) or not isinstance(data.get("codes"), dict):
            return _empty()
        data.setdefault("v", REGISTRY_VERSION)
        if not isinstance(data.get("legacy"), dict):
            data["legacy"] = {}
        data["codes"] = {k: v for k, v in data["codes"].items() if isinstance(v, dict)}
        return data

    def load(self) -> dict:
        """The registry as on disk (cached until the file changes; do not mutate)."""
        signature = self._signature()
        if signature is None:
            return _empty()
        if self._cache is None or self._cache[0] != signature:
            try:
                text = self.path.read_text()
            except OSError:
                return _empty()
            self._cache = (signature, self._parse(text))
        return self._cache[1]

    def _save(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{FILE_NAME}.{os.getpid()}.tmp")
        with open(tmp, "w") as f:
            f.write(json.dumps(data, indent=1, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._cache = None

    @contextmanager
    def _editing(self):
        """The registry, read under the lock; saved on exit if it changed."""
        with self._lock():
            self._cache = None
            data = json.loads(json.dumps(self.load()))
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                self._save(data)

    # ── reads ───────────────────────────────────────────────────────────

    def get(self, code_id: str) -> Optional[dict]:
        return self.load()["codes"].get(code_id)

    def codes(self) -> Dict[str, dict]:
        """Every code, merged ones included: ``{id: entry}``."""
        return self.load()["codes"]

    @staticmethod
    def _canonical(data: dict, key: Optional[str]) -> Optional[str]:
        if not key:
            return key
        codes = data["codes"]
        code = key if key in codes else data["legacy"].get(key)
        if code is None:
            return key
        seen = set()
        while code in codes and codes[code].get("merged_into") and code not in seen:
            seen.add(code)
            code = codes[code]["merged_into"]
        return code

    def canonical(self, key: Optional[str]) -> Optional[str]:
        """The code a key reports under: an id (legacy strings and merges followed), else the key."""
        return self._canonical(self.load(), key)

    def name_of(self, key: Optional[str]) -> str:
        """What a code key is shown as: its code's current name, else the key itself."""
        data = self.load()
        code = self._canonical(data, key)
        entry = data["codes"].get(code)
        return entry["name"] if entry else (key or "?")

    @staticmethod
    def _find(data: dict, name: str) -> Optional[str]:
        codes = data["codes"]
        live = [(cid, e) for cid, e in codes.items() if not e.get("merged_into")]
        for cid, entry in live:
            if entry.get("name") == name:
                return cid
        # The most recently created code that had the name wins a tie
        for cid, entry in sorted(live, key=lambda kv: str(kv[1].get("created", "")), reverse=True):
            if name in (entry.get("previous_names") or ()):
                return cid
        for cid, entry in codes.items():
            if entry.get("merged_into") and (entry.get("name") == name
                                             or name in (entry.get("previous_names") or ())):
                return CodeRegistry._canonical(data, cid)
        if name in data["legacy"]:
            return CodeRegistry._canonical(data, data["legacy"][name])
        return None

    def find(self, name: str) -> Optional[str]:
        """The code a typed name means: a current name, then a previous one, then a legacy string."""
        name = (name or "").strip()
        if not name:
            return None
        if is_code_id(name) and name in self.codes():
            return self.canonical(name)
        return self._find(self.load(), name)

    def roots_of(self, code_id: str) -> List[str]:
        entry = self.get(code_id) or {}
        return list(entry.get("roots") or ())

    # ── writes ──────────────────────────────────────────────────────────

    @staticmethod
    def _mint(data: dict, name: str, source: str, *, legacy: Iterable[str] = ()) -> str:
        code_id = new_id()
        data["codes"][code_id] = {
            "name": name, "source": source, "created": datetime.now().isoformat(timespec="seconds"),
            "previous_names": [], "legacy": list(legacy)}
        for string in legacy:
            data["legacy"][string] = code_id
        return code_id

    @staticmethod
    def _add_root(entry: dict, root: Optional[str]) -> None:
        if not root:
            return
        roots = entry.setdefault("roots", [])
        if root not in roots and len(roots) < MAX_ROOTS:
            roots.append(root)

    def resolve(self, name: str, *, source: str = DEFAULT, root: Optional[str] = None) -> str:
        """The id a launch or rebill to ``name`` bills to, creating a code only for an unknown name.

        ``source`` is ``human`` when the name was given (``--billing-code``,
        a rebill): a ``default`` code it lands on becomes ``human``.
        ``root`` is the root agent of the tree that now uses the code.
        """
        name = (name or "").strip()
        if not name:
            raise CodeError("a billing code cannot be empty")
        with self._editing() as data:
            code_id = (self._canonical(data, name) if is_code_id(name) and name in data["codes"]
                       else self._find(data, name))
            if code_id is None:
                code_id = self._mint(data, name, source)
            entry = data["codes"][code_id]
            if source == HUMAN and entry.get("source") != HUMAN:
                entry["source"] = HUMAN
            self._add_root(entry, root)
        return code_id

    def use(self, code_id: str, root: Optional[str]) -> None:
        """Record that ``root``'s tree bills to ``code_id`` (a rebill onto it)."""
        if not root or root in self.roots_of(code_id) or len(self.roots_of(code_id)) >= MAX_ROOTS:
            return
        with self._editing() as data:
            entry = data["codes"].get(code_id)
            if entry is not None:
                self._add_root(entry, root)

    def adopt(self, strings: Iterable[str]) -> Dict[str, str]:
        """``{string: id}`` for legacy code strings, minting a code for each one not yet mapped.

        Idempotent. A minted code is named after its string, unless a code
        already has that name (one renamed onto it since): then the string
        plus ``~2``, ``~3``...
        """
        wanted = sorted({s for s in strings if isinstance(s, str) and s and not is_code_id(s)})
        data = self.load()
        if all(s in data["legacy"] for s in wanted):
            return {s: self._canonical(data, data["legacy"][s]) for s in wanted}
        with self._editing() as data:
            names = {e.get("name") for e in data["codes"].values() if not e.get("merged_into")}
            for string in wanted:
                if string in data["legacy"]:
                    continue
                name, n = string, 1
                while name in names:
                    n += 1
                    name = f"{string}~{n}"
                names.add(name)
                self._mint(data, name, HUMAN, legacy=[string])
            return {s: self._canonical(data, data["legacy"][s]) for s in wanted}

    def mark_migrated(self) -> None:
        if self.load().get("migrated"):
            return
        with self._editing() as data:
            data["migrated"] = True

    def migrated(self) -> bool:
        return bool(self.load().get("migrated"))

    def _target(self, data: dict, what: str) -> str:
        code_id = (self._canonical(data, what) if is_code_id(what) and what in data["codes"]
                   else self._find(data, what.strip()))
        if code_id is None:
            raise CodeError(f"no billing code '{what}'")
        return code_id

    @staticmethod
    def _rename(data: dict, code_id: str, new: str) -> str:
        entry = data["codes"][code_id]
        old = entry["name"]
        if new != old:
            previous = [n for n in entry.get("previous_names") or () if n != new]
            if old not in previous:
                previous.append(old)
            entry.update(name=new, previous_names=previous)
        return old

    def rename(self, what: str, new: str) -> Tuple[str, str]:
        """Rename a code (by name, old name or id); returns ``(id, old name)``.

        The code becomes ``human``. Refuses a name another code has now.
        """
        new = (new or "").strip()
        if not new:
            raise CodeError("a billing code cannot be empty")
        if is_code_id(new):
            raise CodeError(f"'{new}' looks like a code id; pick another name")
        with self._editing() as data:
            code_id = self._target(data, what)
            for other, entry in data["codes"].items():
                if other != code_id and not entry.get("merged_into") and entry.get("name") == new:
                    raise CodeError(f"'{new}' is taken by another billing code "
                                    f"(overcode billing merge CODE --into {new} folds into it)")
            old = self._rename(data, code_id, new)
            data["codes"][code_id]["source"] = HUMAN
        return code_id, old

    def merge(self, what: str, into: str) -> Tuple[str, str]:
        """Fold code ``what`` into ``into``; returns ``(from id, into id)``.

        No row changes: ``what``'s rows report under ``into``, and its
        names become previous names of ``into``.
        """
        target, folded = self.merge_many([what], into)
        return folded[0][0], target

    def merge_many(self, whats: Iterable[str], into: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Fold every code in ``whats`` into the existing code ``into`` (one write).

        Refuses (nothing merged) an unknown code, or one that is ``into``
        itself. Returns ``(into id, [(id, name)...])`` of the codes folded.
        """
        with self._editing() as data:
            target = self._target(data, into)
            return target, self._fold(
                data, whats, target, lambda what: f"'{what}' and '{into}' are the same billing code")

    def _fold(self, data: dict, whats: Iterable[str], target: str, same) -> List[Tuple[str, str]]:
        """Merge ``whats`` into ``target`` inside one edit; ``same(what)`` words the refusal."""
        sources = []
        for what in whats:
            code_id = self._target(data, what)
            if code_id == target:
                raise CodeError(same(what))
            if code_id not in sources:
                sources.append(code_id)
        folded = []
        for code_id in sources:
            folded.append((code_id, data["codes"][code_id]["name"]))
            self._merge(data, code_id, target)
        return folded

    @staticmethod
    def _merge(data: dict, source: str, target: str) -> None:
        entry, kept = data["codes"][source], data["codes"][target]
        previous = list(kept.get("previous_names") or ())
        added = []
        for name in [entry["name"], *(entry.get("previous_names") or ())]:
            if name not in previous and name != kept["name"]:
                previous.append(name)
                added.append(name)
        kept["previous_names"] = previous
        for root in entry.get("roots") or ():
            CodeRegistry._add_root(kept, root)
        entry["merged_into"] = target
        entry["merged"] = datetime.now().isoformat(timespec="seconds")
        entry["merge_added"] = added  # what unmerge takes back off ``target``

    def collapse(self, whats: Iterable[str], into: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Merge every code in ``whats`` into the code named ``into`` (one write).

        ``into`` is ``billing.default_code``, created as a ``human`` code on
        first use. Refuses (nothing merged) a code that is ``into`` itself
        or unknown. Returns ``(into id, [(id, name)...])`` of the codes folded.
        """
        into = (into or "").strip()
        if not into:
            raise CodeError("no default billing code to collapse into")
        target = self.resolve(into, source=HUMAN)
        with self._editing() as data:
            folded = self._fold(data, whats, target,
                                lambda what: f"'{what}' is the default billing code; it cannot be collapsed")
        return target, folded

    def find_merged(self, what: str) -> Optional[str]:
        """The merged code ``what`` names (its id, name or a previous name); the latest merge wins."""
        return self._find_merged(self.load(), what)

    @staticmethod
    def _find_merged(data: dict, what: str) -> Optional[str]:
        what = (what or "").strip()
        codes = data["codes"]
        if what in codes:
            return what if codes[what].get("merged_into") else None
        merged = sorted(((cid, e) for cid, e in codes.items() if e.get("merged_into")),
                        key=lambda kv: str(kv[1].get("merged", "")), reverse=True)
        for cid, entry in merged:
            if entry.get("name") == what:
                return cid
        for cid, entry in merged:
            if what in (entry.get("previous_names") or ()):
                return cid
        return None

    def unmerge(self, what: str) -> Tuple[str, str, str]:
        """Undo a merge: the code reports its own rows again; returns ``(id, name, was merged into)``.

        Registry only, like the merge. Its names come back off the code it
        was merged into; agents moved over at the merge stay there. A name
        another code has taken since gets ``~2``, ``~3``...
        """
        with self._editing() as data:
            code_id = self._find_merged(data, what)
            if code_id is None:
                raise CodeError(f"no merged billing code '{what}'")
            entry = data["codes"][code_id]
            into = entry["merged_into"]
            kept = data["codes"].get(into)
            names = entry.get("merge_added")
            if names is None:  # merged before merges recorded it
                names = [entry["name"], *(entry.get("previous_names") or ())]
            if kept is not None:
                kept["previous_names"] = [n for n in kept.get("previous_names") or ()
                                          if n not in names]
            for key in ("merged_into", "merged", "merge_added"):
                entry.pop(key, None)
            taken = {e.get("name") for cid, e in data["codes"].items()
                     if cid != code_id and not e.get("merged_into")}
            base, name, n = entry["name"], entry["name"], 1
            while name in taken:
                n += 1
                name = f"{base}~{n}"
            if name != base:
                self._rename(data, code_id, name)
            into_name = kept["name"] if kept is not None else into
        return code_id, name, into_name

    def follow_agent_name(self, code_id: str, name: str) -> bool:
        """Rename a ``default`` code to its agent's new name, if that is safe; True if it now has it.

        For auto-naming (#542): only a ``default`` code that one agent tree
        alone has used, and only to a name no other code answers to. A
        ``human`` code never changes by itself. It stays ``default``.
        """
        name = (name or "").strip()
        if not name or is_code_id(name):
            return False
        data = self.load()
        entry = data["codes"].get(code_id)
        if entry is None:
            return False
        if entry.get("name") == name:
            return True
        with self._editing() as data:
            entry = data["codes"].get(code_id)
            if (entry is None or entry.get("merged_into") or entry.get("source") != DEFAULT
                    or len(entry.get("roots") or ()) != 1):
                return False
            other = self._find(data, name)
            if other is not None and other != code_id:
                return False
            self._rename(data, code_id, name)
        return True
