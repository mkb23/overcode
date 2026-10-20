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


# The namer's view of the task (#542, auto-naming §4): the launch prompt's
# first bytes, written once at launch and never moved.
BRIEF_BYTES = 2048


def write_brief(tmux_session: str, session_id: str, prompt: str) -> None:
    """Keep the first ``BRIEF_BYTES`` of the launch prompt as the agent's brief; never raises."""
    if not prompt or not prompt.strip():
        return
    text = prompt.encode("utf-8")[:BRIEF_BYTES].decode("utf-8", "ignore")
    try:
        agent_dir(tmux_session, session_id).mkdir(parents=True, exist_ok=True)
        agent_file(tmux_session, session_id, "brief").write_text(text, encoding="utf-8")
    except OSError:
        pass  # the namer falls back to the prompts and the pane


def read_brief(tmux_session: str, session_id: str) -> str:
    """The agent's brief, or "" when it has none."""
    try:
        return agent_file(tmux_session, session_id, "brief").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
