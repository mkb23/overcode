"""Where overcode keeps its files. Every overcode path is built here.

Two environment variables move the tree (tests and containers use them;
a default install sets neither):

``OVERCODE_DIR``
    overcode's data directory, default ``~/.overcode``. It holds
    ``config.yaml``, ``presets.json``, ``jobs/``, the wrappers, skills,
    caches and the presence logger, and (by default) the sessions root.

``OVERCODE_STATE_DIR``
    The sessions root, default ``$OVERCODE_DIR/sessions``. It holds
    ``sessions.json`` and the session archive, and one directory per tmux
    session (``<root>/<tmux_session>/``) with that session's daemon state
    and the per-agent files the hooks write. Hook handlers and plugins
    running inside an agent's process (including inside a container, where
    the session directory is mounted at ``<root>/<tmux_session>``) are
    given this variable so they write where the daemon reads. The bundled
    opencode plugins read it with the same meaning.

Before 0.6.0 ``OVERCODE_STATE_DIR`` also meant "the parent of
``sessions/``" to ``SessionManager`` and "the parent of ``jobs/``" to
``JobManager``. A tree written that way is still read: see
:func:`sessions_registry_dir` and :func:`jobs_dir`.

This module imports nothing from overcode: the hook handler runs it on
every agent event.
"""

import os
from pathlib import Path

# The per-agent files in a session directory, keyed by the agent's name
# (``kind`` -> file name pattern).
AGENT_FILES = {
    "hook_state": "hook_state_{}.json",        # hook_handler / plugins -> detector
    "hook_events": "hook_events_{}.jsonl",     # append-only hook event log (#448)
    "report": "report_{}.json",                # `overcode report` from a child
    "episodes": "episodes_{}.jsonl",           # engine episode log
    "rename_notice": "rename_notice_{}.txt",   # one-shot note after a rename (#478)
    "heartbeat": "heartbeat_{}.last",          # last heartbeat (time context)
}


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


def agent_file_name(name: str, kind: str) -> str:
    """The file name of one agent's ``kind`` file (see :data:`AGENT_FILES`)."""
    return AGENT_FILES[kind].format(name)


def agent_file(tmux_session: str, name: str, kind: str) -> Path:
    """The path of agent ``name``'s ``kind`` file in its session directory."""
    return session_dir(tmux_session) / AGENT_FILES[kind].format(name)
