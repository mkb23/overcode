"""An agent's own directory: made at launch, reset on relaunch, removed at the end.

Every agent keeps its per-agent files (hook state and event log, report,
episode log, rename notice) in ``<session dir>/agents/<session id>/``
(``paths.agent_dir``, quality review item 35). The directory exists from
launch until the agent is archived, when its files are packed into the day
bundle (``agent_bundle``) and the directory is removed.

Its existence is the signal readers go by (``paths.agent_path``): an agent
with a directory has its files there; one without was launched before
per-agent directories and still writes name-keyed files until it is
relaunched.
"""

import os
import shutil

from .paths import agent_dir, agent_file

# Where a relaunch moves the report the previous run filed, so the new run
# does not settle from it (kept for whoever wants to read it).
PREVIOUS_REPORT = "report.previous.json"


def prepare_agent_dir(tmux_session: str, session_id: str, *, reset: bool = False) -> None:
    """Make the agent's directory before its process starts; never raises.

    ``reset`` is a relaunch of the same agent (restart, revive): its hook
    state goes and its report is moved aside, so the new process is not
    read through what the old one left: a Stop that settles a revived
    child at once, or a report that marks it done.
    """
    directory = agent_dir(tmux_session, session_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    if not reset:
        return
    try:
        agent_file(tmux_session, session_id, "hook_state").unlink(missing_ok=True)
    except OSError:
        pass
    report = agent_file(tmux_session, session_id, "report")
    try:
        os.replace(report, directory / PREVIOUS_REPORT)
    except OSError:
        pass  # none filed


def remove_agent_dir(tmux_session: str, session_id: str) -> None:
    """Remove the agent's directory and everything in it; never raises."""
    shutil.rmtree(agent_dir(tmux_session, session_id), ignore_errors=True)
