"""Where overcode keeps its files. Every overcode path is built here.

Two environment variables move the tree (tests and containers use them;
a default install sets neither):

``OVERCODE_DIR``
    overcode's data directory, default ``~/.overcode``. It holds
    ``config.yaml``, ``presets.json``, ``jobs/``, the wrappers, skills,
    caches and the presence logger, and (by default) the sessions root.

``OVERCODE_STATE_DIR``
    The sessions root, default ``$OVERCODE_DIR/sessions``. It holds
    ``sessions.json`` and one directory per tmux session
    (``<root>/<tmux_session>/``) with that session's daemon state, one
    directory per live agent (``agents/<session id>/``, the files the hooks
    write) and the day bundles of archived agents' files (``bundles/``).
    Hook handlers and plugins running inside an agent's process (including
    inside a container, where the session directory is mounted at
    ``<root>/<tmux_session>``) are given this variable so they write where
    the daemon reads. The bundled opencode plugins read it with the same
    meaning.

The session archive (``archive/``) and the spend ledger (``ledger/``) sit
beside the sessions root (``~/.overcode/archive``), never inside it where
they would be taken for tmux sessions: see :func:`data_dir_for`.

Before 0.6.0 ``OVERCODE_STATE_DIR`` also meant "the parent of
``sessions/``" to ``SessionManager`` and "the parent of ``jobs/``" to
``JobManager``. A tree written that way is still read: see
:func:`sessions_registry_dir` and :func:`jobs_dir`.

This module imports nothing from overcode: the hook handler runs it on
every agent event.
"""

import os
from pathlib import Path

# The per-agent files, by kind. An agent's files live in its own directory,
# ``<session dir>/agents/<overcode session id>/`` (``agent_dir``), so a new
# agent never sees files an earlier agent of the same name left, and a
# rename moves nothing (quality review item 35).
AGENT_DIR_FILES = {
    "hook_state": "hook_state.json",        # hook_handler / plugins -> detector
    "hook_events": "hook_events.jsonl",     # append-only hook event log (#448)
    "report": "report.json",                # `overcode report` from a child
    "episodes": "episodes.jsonl",           # engine episode log
    "rename_notice": "rename_notice.txt",   # one-shot note after a rename (#478)
    "heartbeat": "heartbeat.last",          # last heartbeat (time context)
}

# Before 0.6.0 the same files sat directly in the session directory, keyed
# by the agent's name (``kind`` -> file name pattern). Still read for an
# agent launched before the upgrade (one with no ``agent_dir``) until it is
# relaunched; ``agent_path`` picks the right one.
AGENT_FILES = {
    "hook_state": "hook_state_{}.json",
    "hook_events": "hook_events_{}.jsonl",
    "report": "report_{}.json",
    "episodes": "episodes_{}.jsonl",
    "rename_notice": "rename_notice_{}.txt",
    "heartbeat": "heartbeat_{}.last",
}

# Directory names that are never a tmux session's: the archive and the
# ledger sit among the session directories when they cannot go one level
# up (see ``data_dir_for``).
RESERVED_DIR_NAMES = frozenset({"archive", "ledger"})


def overcode_dir() -> Path:
    """overcode's data directory: ``$OVERCODE_DIR``, default ``~/.overcode``."""
    env = os.environ.get("OVERCODE_DIR")
    return Path(env) if env else Path.home() / ".overcode"


def sessions_root() -> Path:
    """The sessions root: ``$OVERCODE_STATE_DIR``, default ``overcode_dir()/sessions``."""
    env = os.environ.get("OVERCODE_STATE_DIR")
    return Path(env) if env else overcode_dir() / "sessions"


def session_dir(tmux_session: str) -> Path:
    """The directory of one tmux session's daemon state and agent files."""
    return sessions_root() / tmux_session


def sessions_registry_dir() -> Path:
    """The directory holding ``sessions.json`` and the session archive.

    The sessions root, except for a tree written while
    ``OVERCODE_STATE_DIR`` meant the parent of ``sessions/``: when the root
    has no ``sessions.json`` but ``<root>/sessions/sessions.json`` exists,
    that older directory is used.
    """
    root = sessions_root()
    if os.environ.get("OVERCODE_STATE_DIR"):
        legacy = root / "sessions"
        if not (root / "sessions.json").exists() and (legacy / "sessions.json").exists():
            return legacy
    return root


def jobs_dir() -> Path:
    """The directory holding ``jobs.json`` and the jobs' exit-code files.

    ``overcode_dir()/jobs``, except for a tree written while
    ``OVERCODE_STATE_DIR`` meant the parent of ``jobs/``: when there is no
    ``jobs.json`` there but ``<sessions root>/jobs/jobs.json`` exists, that
    older directory is used.
    """
    current = overcode_dir() / "jobs"
    if os.environ.get("OVERCODE_STATE_DIR"):
        legacy = sessions_root() / "jobs"
        if not (current / "jobs.json").exists() and (legacy / "jobs.json").exists():
            return legacy
    return current


def config_file() -> Path:
    """The user's ``config.yaml``."""
    return overcode_dir() / "config.yaml"


def legacy_agent_file_name(name: str, kind: str) -> str:
    """The pre-0.6.0 name-keyed file name of agent ``name``'s ``kind`` file."""
    return AGENT_FILES[kind].format(name)


def legacy_agent_file(tmux_session: str, name: str, kind: str) -> Path:
    """The pre-0.6.0 path of agent ``name``'s ``kind`` file (see :data:`AGENT_FILES`)."""
    return session_dir(tmux_session) / AGENT_FILES[kind].format(name)


def agents_dir(tmux_session: str) -> Path:
    """The directory holding one directory per agent of a tmux session."""
    return session_dir(tmux_session) / "agents"


def agent_dir(tmux_session: str, session_id: str) -> Path:
    """Agent ``session_id``'s own directory: its hook state, report and logs."""
    return agents_dir(tmux_session) / session_id


def agent_file(tmux_session: str, session_id: str, kind: str) -> Path:
    """The path of agent ``session_id``'s ``kind`` file (see :data:`AGENT_DIR_FILES`)."""
    return agent_dir(tmux_session, session_id) / AGENT_DIR_FILES[kind]


def agent_path(
    tmux_session: str,
    session_id: "str | None",
    name: "str | None",
    kind: str,
    *,
    base: "Path | None" = None,
) -> Path:
    """Where agent (``session_id``, ``name``)'s ``kind`` file is, to read or write.

    The file in the agent's own directory, unless that directory does not
    exist and the agent has a name: then the name-keyed file of an agent
    launched before per-agent directories, whose hooks keep writing there
    until it is relaunched. Every launch creates the directory, so an agent
    launched since never reads a file an earlier agent of its name left.
    The common case costs one ``stat``. ``base`` stands in for the tmux
    session's directory (a detector built on another one).
    """
    directory = Path(base) if base is not None else session_dir(tmux_session)
    if session_id:
        own_dir = directory / "agents" / session_id
        own = own_dir / AGENT_DIR_FILES[kind]
        if not name:
            return own
        try:
            os.stat(own)
            return own
        except OSError:
            pass
        if os.path.isdir(own_dir):
            return own
    if name:
        return directory / AGENT_FILES[kind].format(name)
    raise ValueError("an agent's path needs its session id or its name")


def bundles_dir(tmux_session: str) -> Path:
    """Where a tmux session's archived agents' files are kept, one file per day."""
    return session_dir(tmux_session) / "bundles"


def bundle_path(tmux_session: str, day: str) -> Path:
    """The bundle of the agents of ``tmux_session`` archived on ``day`` (YYYY-MM-DD)."""
    return bundles_dir(tmux_session) / f"{day}.agents.gz"


def data_dir_for(registry_dir: Path) -> Path:
    """Where the archive and the ledger live, for a sessions registry directory.

    One level up from a registry called ``sessions`` (the default:
    ``~/.overcode``), so neither is ever taken for a tmux session's
    directory; the registry directory itself otherwise (a custom
    ``OVERCODE_STATE_DIR``, a test's directory), where
    :data:`RESERVED_DIR_NAMES` keeps them apart from the session
    directories.
    """
    registry_dir = Path(registry_dir)
    return registry_dir.parent if registry_dir.name == "sessions" else registry_dir


def archive_dir(registry_dir: "Path | None" = None) -> Path:
    """The session archive: one JSONL file per month (:func:`archive_month_path`)."""
    return data_dir_for(registry_dir or sessions_registry_dir()) / "archive"


def archive_month_path(month: str, registry_dir: "Path | None" = None) -> Path:
    """The archive file of ``month`` (YYYY-MM); a closed month is gzipped (``.gz``)."""
    return archive_dir(registry_dir) / f"{month}.jsonl"


def ledger_dir(registry_dir: "Path | None" = None) -> Path:
    """The spend ledger: one JSONL file per month, kept for ever."""
    return data_dir_for(registry_dir or sessions_registry_dir()) / "ledger"


def ledger_month_path(month: str, registry_dir: "Path | None" = None) -> Path:
    """The ledger file of ``month`` (YYYY-MM); a closed month is gzipped (``.gz``)."""
    return ledger_dir(registry_dir) / f"{month}.jsonl"


def ledger_totals_path(registry_dir: "Path | None" = None) -> Path:
    """The ledger's running totals per billing code."""
    return ledger_dir(registry_dir) / "totals.json"
