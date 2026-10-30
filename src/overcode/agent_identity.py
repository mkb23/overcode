"""
Which agent this process runs in (auto-naming §1).

An agent's process carries ``OVERCODE_SESSION_ID`` and
``OVERCODE_SESSION_NAME`` from its launch. A rename does not restart it, so
the env name is only the name it was launched under: the agent is the id.
The name identifies it only for an agent launched without an id (pre-0.6),
and then follows renames (``resolve_session_name``).
"""

import os
from typing import Mapping, Optional


def env_agent_id(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """This process's agent id (``OVERCODE_SESSION_ID``), or None."""
    return (os.environ if env is None else env).get("OVERCODE_SESSION_ID") or None


def env_agent_name(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The name this process's agent was launched under, or None."""
    return (os.environ if env is None else env).get("OVERCODE_SESSION_NAME") or None


# Set in a job an agent started (``overcode bash``): an agent the job
# launches is that agent's child. Not OVERCODE_SESSION_ID: the job is not
# the agent (its reports, hooks and state access are its own).
JOB_OWNER_VAR = "OVERCODE_JOB_OWNER_ID"


def env_job_owner_id(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The id of the agent whose job this process runs in, or None."""
    return (os.environ if env is None else env).get(JOB_OWNER_VAR) or None


def in_agent(env: Optional[Mapping[str, str]] = None) -> bool:
    """True inside an agent overcode launched (either variable set)."""
    return bool(env_agent_id(env) or env_agent_name(env))


def current_agent(sessions, env: Optional[Mapping[str, str]] = None):
    """This process's agent record from ``sessions`` (a SessionManager), or None.

    By id; by the launch name, following renames, only with no id. An id
    with no record is no agent: its launch name may belong to another by now.
    """
    sid = env_agent_id(env)
    if sid:
        return sessions.get_session(sid)
    name = env_agent_name(env)
    return sessions.resolve_session_name(name) if name else None


def find_in_daemon_state(state: dict, session_id: Optional[str],
                         name: Optional[str]) -> Optional[dict]:
    """An agent's entry in a parsed ``monitor_daemon_state.json``: by id, else by name."""
    key, value = ("session_id", session_id) if session_id else ("name", name)
    for s in state.get("sessions", []):
        if s.get(key) == value:
            return s
    return None
