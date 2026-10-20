"""
Where an agent's name came from (#542, docs/design/auto-naming.md §2).

``Session.name_source`` is one of:

    default  the TUI modal's directory basename, a fork's ``-fork`` name,
             or a pre-feature record that looks like one
    parent   ``-n`` given by an agent launching a child
    auto     overcode named it
    human    a human chose it (``-n`` from a shell, a rename, a typed name)

``parent`` and ``human`` lock the name: the namer never renames it.
"""

import re
from pathlib import Path
from typing import Mapping, Optional

DEFAULT = "default"
PARENT = "parent"
AUTO = "auto"
HUMAN = "human"
NAME_SOURCES = (DEFAULT, PARENT, AUTO, HUMAN)

# The TUI marks names overcode chose (the name column: dim / "~")
AUTO_MARKER = "~"


def is_locked(source: Optional[str]) -> bool:
    """Whether the namer must leave this name alone."""
    return source not in (DEFAULT, AUTO)


def name_marker(source: Optional[str]) -> str:
    """The text that leads the name in the name column: "~" for an auto name."""
    return AUTO_MARKER if source == AUTO else ""


def launched_from_agent(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether this process runs inside an overcode agent (``OVERCODE_SESSION_ID`` set)."""
    import os
    env = os.environ if environ is None else environ
    return bool(env.get("OVERCODE_SESSION_ID"))


def cli_source(environ: Optional[Mapping[str, str]] = None) -> str:
    """The source of a ``-n`` given on the command line: ``parent`` inside an agent, else ``human``."""
    return PARENT if launched_from_agent(environ) else HUMAN


def _is_basename_default(name: str, start_directory: Optional[str]) -> bool:
    """``name`` is what the new-agent modal derives from the directory (``_unique_name``)."""
    if not start_directory:
        return False
    base = Path(start_directory).name
    if not base:
        return False
    return bool(re.fullmatch(re.escape(base) + r"(?:[2-9]|[1-9][0-9])?|" + re.escape(base) + "_new",
                             name))


def is_fork_default(name: str, source_name: Optional[str] = None) -> bool:
    """``name`` is a fork's default: ``<source>-fork``, maybe numbered (``-fork2``).

    Without ``source_name``, any name ending so.
    """
    stem = re.escape(source_name) if source_name else ".+"
    return bool(re.fullmatch(stem + r"-fork(?:[0-9]+|_[0-9]+)?", name))


def infer_legacy(record: Mapping) -> str:
    """The source of a record written before ``name_source`` existed.

    Conservative: ``default`` only for a name that is exactly what overcode
    would have made up (the modal's directory basename, with its 2..99 or
    ``_new`` suffix, or a fork's ``-fork``); anything else is ``human``, so
    a name a person or a parent chose is never renamed.
    """
    name = record.get("name")
    if not isinstance(name, str) or not name:
        return HUMAN
    start = record.get("start_directory")
    if _is_basename_default(name, start if isinstance(start, str) else None):
        return DEFAULT
    if is_fork_default(name):
        return DEFAULT
    return HUMAN


def auto_rename_toast(before, after) -> Optional[str]:
    """The TUI's toast when the namer renamed an agent (§8): ``old → new (auto)``, else None."""
    if before is None or after is None or before.name == after.name:
        return None
    if getattr(after, "name_source", None) != AUTO:
        return None
    return f"{before.name} → {after.name} (auto)"
