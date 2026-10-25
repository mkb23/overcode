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
``JobManager``, and both ignored ``OVERCODE_DIR``: with only it set,
``sessions.json`` and ``jobs.json`` stayed under ``~/.overcode``. A tree
written either way is still read: see :func:`sessions_registry_dir` and
:func:`jobs_dir`.

This module imports nothing from overcode: the hook handler runs it on
every agent event.

The real-state guard
--------------------
A script that imports overcode as a library and runs inside an overcode
agent (``OVERCODE_SESSION_ID`` / ``OVERCODE_SESSION_NAME`` set: an agent's
shell, a review or repro script it runs) must not read or write the user's
real tree: a repro script once registered fake sessions, archive records
and ledger rows there. So :func:`overcode_dir` and :func:`sessions_root`,
which every path goes through, raise :class:`RealStateRefused` when they
resolve to the real default tree (``~/.overcode``, however reached) and

- the process was not started by one of overcode's own entry points
  (they call :func:`mark_entry_point`; any ``python -m overcode.<module>``
  counts too), and
- it runs inside an agent, and
- ``OVERCODE_ALLOW_REAL_STATE=1`` is not set.

Reads are refused as well as writes: the check is on resolution, the one
place every path is built. A human's own shell (no agent variables) is
never refused. The mark is a process-local flag, never an environment
variable, so it does not leak into an agent's shell through the processes
overcode starts.
"""

import os
import sys
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
    # The namer's inputs and state (#542, auto-naming §4); no name-keyed form
    "brief": "brief.txt",                   # first 2 KB of the launch prompt
    "prompts": "prompts.jsonl",             # first 3 user prompts, 1 KB each
    "recent_prompts": "recent_prompts.jsonl",  # last 3 user prompts, 1 KB each (#547)
    "namer": "namer.json",                  # the daemon namer's per-agent state
    "child_notice": "child_notice.txt",     # one-shot "your child X is now Y"
}

# Before 0.6.0 the same files sat directly in the session directory, keyed
# by the agent's name (``kind`` -> file name pattern; files added since have
# none). Still read for an
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

# Directory names that are never a tmux session's: the archive, the
# ledger and the subscription usage data sit among the session directories
# when they cannot go one level up (see ``data_dir_for``).
RESERVED_DIR_NAMES = frozenset({"archive", "ledger", "usage"})


class RealStateRefused(RuntimeError):
    """A script inside an agent resolved the real state dir (see the module doc)."""


REFUSAL_MESSAGE = (
    "overcode: refusing to use the real state dir from a script inside an "
    "agent; set OVERCODE_STATE_DIR/OVERCODE_DIR to a temp dir, or "
    "OVERCODE_ALLOW_REAL_STATE=1 if you mean it"
)

# Set by overcode's own entry points (the ``overcode`` command, the daemons
# and servers it spawns): this process may use the real tree.
_entry_point = False


def mark_entry_point() -> None:
    """Mark this process as one of overcode's own entry points.

    Called first thing by ``entrypoint.main`` (the ``overcode`` command,
    hook-handler fast path included), ``cli.main`` (``python -m
    overcode.cli``), the daemons' and servers' ``main`` and ``run_tui``.
    """
    global _entry_point
    _entry_point = True


def _started_as_overcode_module() -> bool:
    """True when ``__main__`` is an overcode module (``python -m overcode.x``)."""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    name = getattr(spec, "name", None) or ""
    return name == "overcode" or name.startswith("overcode.")


def real_overcode_dir() -> Path:
    """The real default tree: ``~/.overcode`` of the current ``HOME``."""
    return Path.home() / ".overcode"


def _is_real(path: Path) -> bool:
    """True when ``path`` is the real default tree or inside it."""
    real = os.path.realpath(real_overcode_dir())
    resolved = os.path.realpath(path)
    return resolved == real or resolved.startswith(real + os.sep)


def guard_real_state(path: Path) -> Path:
    """``path``, unless a script inside an agent resolved the real tree.

    Raises :class:`RealStateRefused` then (see the module doc). The common
    cases (an entry point, a shell outside any agent) cost one flag test
    and two dictionary lookups.
    """
    if _entry_point:
        return path
    env = os.environ
    if not (env.get("OVERCODE_SESSION_ID") or env.get("OVERCODE_SESSION_NAME")):
        return path
    if env.get("OVERCODE_ALLOW_REAL_STATE") == "1":
        return path
    if _started_as_overcode_module() or not _is_real(path):
        return path
    raise RealStateRefused(REFUSAL_MESSAGE)


def overcode_dir() -> Path:
    """overcode's data directory: ``$OVERCODE_DIR``, default ``~/.overcode``."""
    env = os.environ.get("OVERCODE_DIR")
    return guard_real_state(Path(env) if env else Path.home() / ".overcode")


def sessions_root() -> Path:
    """The sessions root: ``$OVERCODE_STATE_DIR``, default ``overcode_dir()/sessions``."""
    env = os.environ.get("OVERCODE_STATE_DIR")
    return guard_real_state(Path(env)) if env else overcode_dir() / "sessions"


def session_dir(tmux_session: str) -> Path:
    """The directory of one tmux session's daemon state and agent files."""
    return sessions_root() / tmux_session


def _pre_06_home_dir(name: str) -> "Path | None":
    """``~/.overcode/<name>`` when 0.5.x kept the registry there and 0.6 would not.

    Before 0.6.0, ``SessionManager`` and ``JobManager`` ignored
    ``OVERCODE_DIR``: with it set and ``OVERCODE_STATE_DIR`` unset they
    kept ``sessions.json`` and ``jobs.json`` under ``~/.overcode``. None
    unless that is the setup now (otherwise the default tree is the same).
    """
    if os.environ.get("OVERCODE_STATE_DIR") or not os.environ.get("OVERCODE_DIR"):
        return None
    home = Path.home() / ".overcode"
    if overcode_dir() == home:
        return None
    return home / name


def _registry_or_legacy(current: Path, legacies, file_name: str) -> Path:
    """``current``, or the first legacy directory holding ``file_name`` when it has none."""
    if (current / file_name).exists():
        return current
    for legacy in legacies:
        if legacy is not None and (legacy / file_name).exists():
            return guard_real_state(legacy)
    return current


def sessions_registry_dir() -> Path:
    """The directory holding ``sessions.json`` and the session archive.

    The sessions root, except for a tree an older overcode wrote, when the
    root has no ``sessions.json`` and the older directory has one:

    - ``<root>/sessions``, from when ``OVERCODE_STATE_DIR`` meant the
      parent of ``sessions/``;
    - ``~/.overcode/sessions``, from when ``OVERCODE_DIR`` alone did not
      move ``sessions.json`` (see :func:`_pre_06_home_dir`).
    """
    root = sessions_root()
    legacies = [
        root / "sessions" if os.environ.get("OVERCODE_STATE_DIR") else None,
        _pre_06_home_dir("sessions"),
    ]
    return _registry_or_legacy(root, legacies, "sessions.json")


def jobs_dir() -> Path:
    """The directory holding ``jobs.json`` and the jobs' exit-code files.

    ``overcode_dir()/jobs``, except for a tree an older overcode wrote,
    when there is no ``jobs.json`` there and the older directory has one:

    - ``<sessions root>/jobs``, from when ``OVERCODE_STATE_DIR`` meant the
      parent of ``jobs/``;
    - ``~/.overcode/jobs``, from when ``OVERCODE_DIR`` alone did not move
      ``jobs.json`` (see :func:`_pre_06_home_dir`).
    """
    legacies = [
        sessions_root() / "jobs" if os.environ.get("OVERCODE_STATE_DIR") else None,
        _pre_06_home_dir("jobs"),
    ]
    return _registry_or_legacy(overcode_dir() / "jobs", legacies, "jobs.json")


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


def usage_dir(registry_dir: "Path | None" = None) -> Path:
    """Subscription quota data (``quota_engine``): usage history per account,
    the token stream, the fitted model and statusline samples."""
    return data_dir_for(registry_dir or sessions_registry_dir()) / "usage"


def transcripts_dir() -> Path:
    """The transcript archive: byte-exact zstd copies of Claude Code's
    transcripts and their manifest (``transcript_archive``). Private (700)."""
    return overcode_dir() / "transcripts"
