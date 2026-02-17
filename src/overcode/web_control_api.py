"""
Control action handlers for the web API.

Thin dispatch layer — each function takes parsed JSON body fields,
calls existing Launcher/SessionManager/daemon utilities, returns a result dict.

All functions return {"ok": True, ...} on success or raise ControlError on failure.
"""

import subprocess
import sys
from typing import Optional


class ControlError(Exception):
    """Raised when a control action fails."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _get_session_or_error(session_manager, name: str):
    """Look up a session by name, raise 404 if not found."""
    session = session_manager.get_session_by_name(name)
    if session is None:
        raise ControlError(f"Agent '{name}' not found", status=404)
    return session


# ---------------------------------------------------------------------------
# Agent Interaction
# ---------------------------------------------------------------------------


def send_to_agent(
    tmux_session: str, name: str, text: str, enter: bool = True
) -> dict:
    """Send instruction or text to an agent. Auto-wakes sleeping agents."""
    from .launcher import ClaudeLauncher
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    # Auto-wake sleeping agent (#168)
    if session.is_asleep:
        sm.update_session(session.id, is_asleep=False)

    launcher = ClaudeLauncher(tmux_session=tmux_session, session_manager=sm)
    if launcher.send_to_session(name, text, enter=enter):
        return {"ok": True}
    raise ControlError("Failed to send to agent", status=500)


def send_key_to_agent(tmux_session: str, name: str, key: str) -> dict:
    """Send a special key to an agent."""
    from .tmux_manager import TmuxManager
    from .session_manager import SessionManager

    allowed_keys = {
        "enter": ("", True),
        "escape": ("Escape", False),
        "tab": ("Tab", False),
        "up": ("Up", False),
        "down": ("Down", False),
        "1": ("1", False),
        "2": ("2", False),
        "3": ("3", False),
        "4": ("4", False),
        "5": ("5", False),
    }

    key_lower = key.lower().strip()
    if key_lower not in allowed_keys:
        raise ControlError(
            f"Invalid key: '{key}'. Allowed: {', '.join(sorted(allowed_keys))}"
        )

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    tmux = TmuxManager(tmux_session)
    send_text, send_enter = allowed_keys[key_lower]
    if send_text == "" and send_enter:
        success = tmux.send_keys(session.tmux_window, "", enter=True)
    else:
        success = tmux.send_keys(session.tmux_window, send_text, enter=send_enter)

    if success:
        return {"ok": True}
    raise ControlError("Failed to send key to agent", status=500)


def kill_agent(tmux_session: str, name: str, cascade: bool = True) -> dict:
    """Kill an agent (and children by default)."""
    from .launcher import ClaudeLauncher
    from .session_manager import SessionManager

    sm = SessionManager()
    _get_session_or_error(sm, name)

    launcher = ClaudeLauncher(tmux_session=tmux_session, session_manager=sm)
    if launcher.kill_session(name, cascade=cascade):
        return {"ok": True}
    raise ControlError("Failed to kill agent", status=500)


def restart_agent(tmux_session: str, name: str) -> dict:
    """Ctrl-C + relaunch with same permissions."""
    import os
    from .tmux_manager import TmuxManager
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    # Build the claude command based on permissiveness mode
    claude_command = os.environ.get("CLAUDE_COMMAND", "claude")
    if claude_command == "claude":
        cmd_parts = ["claude", "code"]
    else:
        cmd_parts = [claude_command]

    if session.permissiveness_mode == "bypass":
        cmd_parts.append("--dangerously-skip-permissions")
    elif session.permissiveness_mode == "permissive":
        cmd_parts.extend(["--permission-mode", "dontAsk"])

    cmd_str = " ".join(cmd_parts)

    tmux = TmuxManager(tmux_session)

    # Send Ctrl-C
    if not tmux.send_keys(session.tmux_window, "C-c", enter=False):
        raise ControlError("Failed to send Ctrl-C", status=500)

    # Wait briefly, then send restart command
    import time
    time.sleep(0.5)

    env_prefix = f"OVERCODE_SESSION_NAME={session.name} OVERCODE_TMUX_SESSION={tmux_session}"
    restart_cmd = f"{env_prefix} {cmd_str}"

    if tmux.send_keys(session.tmux_window, restart_cmd, enter=True):
        return {"ok": True}
    raise ControlError("Failed to restart agent", status=500)


def launch_agent(
    tmux_session: str,
    directory: str,
    name: str,
    prompt: Optional[str] = None,
    permissions: str = "normal",
) -> dict:
    """Launch a new agent."""
    from .launcher import ClaudeLauncher
    from .session_manager import SessionManager

    perm_map = {
        "normal": (False, False),
        "permissive": (True, False),
        "bypass": (False, True),
    }
    if permissions not in perm_map:
        raise ControlError(
            f"Invalid permissions: '{permissions}'. Use: normal, permissive, bypass"
        )

    skip_permissions, dangerously_skip = perm_map[permissions]

    sm = SessionManager()
    launcher = ClaudeLauncher(tmux_session=tmux_session, session_manager=sm)
    session = launcher.launch(
        name=name,
        start_directory=directory,
        initial_prompt=prompt,
        skip_permissions=skip_permissions,
        dangerously_skip_permissions=dangerously_skip,
    )
    if session:
        return {"ok": True, "session_id": session.id}
    raise ControlError("Failed to launch agent", status=500)


# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------


def set_standing_orders(
    tmux_session: str, name: str, text: Optional[str] = None, preset: Optional[str] = None
) -> dict:
    """Set standing orders (text or preset name)."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    if preset:
        from .standing_instructions import resolve_instructions
        full_text, preset_name = resolve_instructions(preset)
        if not preset_name:
            raise ControlError(f"Unknown preset: '{preset}'")
        sm.set_standing_instructions(session.id, full_text, preset_name=preset_name)
        return {"ok": True, "preset": preset_name}
    elif text is not None:
        sm.set_standing_instructions(session.id, text)
        return {"ok": True}
    else:
        raise ControlError("Provide 'text' or 'preset'")


def clear_standing_orders(tmux_session: str, name: str) -> dict:
    """Clear standing orders."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.set_standing_instructions(session.id, "", preset_name=None)
    return {"ok": True}


def set_budget(tmux_session: str, name: str, usd: float) -> dict:
    """Set cost budget (0 = unlimited)."""
    from .session_manager import SessionManager

    if usd < 0:
        raise ControlError("Budget cannot be negative")

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.set_cost_budget(session.id, usd)
    return {"ok": True}


def set_value(tmux_session: str, name: str, value: int) -> dict:
    """Set priority value (sort key)."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.set_agent_value(session.id, value)
    return {"ok": True}


def set_annotation(tmux_session: str, name: str, text: str) -> dict:
    """Set human annotation (empty = clear)."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.set_human_annotation(session.id, text)
    return {"ok": True}


def set_sleep(tmux_session: str, name: str, asleep: bool) -> dict:
    """Set sleep state. Rejects if agent is running or has active heartbeat."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    if asleep:
        # Cannot sleep a running agent
        if session.stats.current_state in ("running", "green"):
            raise ControlError("Cannot put a running agent to sleep", status=409)
        # Cannot sleep agent with active heartbeat
        if session.heartbeat_enabled and not session.heartbeat_paused:
            raise ControlError(
                "Cannot sleep agent with active heartbeat — disable heartbeat first",
                status=409,
            )

    sm.update_session(session.id, is_asleep=asleep)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Heartbeat Control
# ---------------------------------------------------------------------------


def configure_heartbeat(
    tmux_session: str,
    name: str,
    enabled: bool = True,
    frequency: Optional[str] = None,
    instruction: Optional[str] = None,
) -> dict:
    """Configure heartbeat fully."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    updates = {"heartbeat_enabled": enabled}

    if frequency is not None:
        freq_seconds = _parse_frequency(frequency)
        if freq_seconds < 30:
            raise ControlError("Heartbeat frequency must be at least 30 seconds")
        updates["heartbeat_frequency_seconds"] = freq_seconds

    if instruction is not None:
        updates["heartbeat_instruction"] = instruction

    sm.update_session(session.id, **updates)
    return {"ok": True}


def pause_heartbeat(tmux_session: str, name: str) -> dict:
    """Pause heartbeat (keep config)."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    if not session.heartbeat_enabled:
        raise ControlError("No heartbeat configured", status=409)
    if session.heartbeat_paused:
        raise ControlError("Heartbeat already paused", status=409)

    sm.update_session(session.id, heartbeat_paused=True)
    return {"ok": True}


def resume_heartbeat(tmux_session: str, name: str) -> dict:
    """Resume paused heartbeat."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)

    if not session.heartbeat_enabled:
        raise ControlError("No heartbeat configured", status=409)
    if not session.heartbeat_paused:
        raise ControlError("Heartbeat not paused", status=409)
    if session.is_asleep:
        raise ControlError("Cannot resume heartbeat on sleeping agent", status=409)

    sm.update_session(session.id, heartbeat_paused=False)
    return {"ok": True}


def _parse_frequency(freq: str) -> int:
    """Parse frequency string like '5m', '300', '1h' to seconds."""
    freq = freq.strip().lower()
    if freq.endswith("m"):
        return int(freq[:-1]) * 60
    elif freq.endswith("h"):
        return int(freq[:-1]) * 3600
    elif freq.endswith("s"):
        return int(freq[:-1])
    else:
        return int(freq)


# ---------------------------------------------------------------------------
# Feature Toggles
# ---------------------------------------------------------------------------


def set_time_context(tmux_session: str, name: str, enabled: bool) -> dict:
    """Toggle time awareness hooks."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.update_session(session.id, time_context_enabled=enabled)
    return {"ok": True}


def set_hook_detection(tmux_session: str, name: str, enabled: bool) -> dict:
    """Toggle hook-based status detection."""
    from .session_manager import SessionManager

    sm = SessionManager()
    session = _get_session_or_error(sm, name)
    sm.update_session(session.id, hook_status_detection=enabled)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bulk Operations
# ---------------------------------------------------------------------------


def transport_all(tmux_session: str) -> dict:
    """Send handover instructions to all active agents."""
    from .launcher import ClaudeLauncher
    from .session_manager import SessionManager

    sm = SessionManager()
    launcher = ClaudeLauncher(tmux_session=tmux_session, session_manager=sm)

    sessions = sm.list_sessions()
    active = [
        s for s in sessions
        if s.tmux_session == tmux_session
        and s.status != "terminated"
        and not s.is_asleep
    ]

    if not active:
        raise ControlError("No active agents to transport", status=409)

    handover_instruction = (
        "Please prepare for handover. Follow these steps in order:\n\n"
        "1. Check your current branch with `git branch --show-current`\n"
        "   - If on main or master, create and switch to a new branch:\n"
        "     `git checkout -b handover/<brief-task-description>`\n"
        "   - Never push directly to main/master\n\n"
        "2. Commit all your current changes with a descriptive commit message\n\n"
        "3. Push to your branch: `git push -u origin <branch-name>`\n\n"
        "4. Check if a PR exists: `gh pr list --head $(git branch --show-current)`\n"
        "   - If no PR exists, create a draft PR:\n"
        "     `gh pr create --draft --title '<brief title>' --body 'WIP'`\n\n"
        "5. Post a handover comment on the PR using `gh pr comment` with:\n"
        "   - What you've accomplished\n"
        "   - Current state of the work\n"
        "   - Any pending tasks or next steps\n"
        "   - Known issues or blockers"
    )

    success_count = 0
    for session in active:
        if launcher.send_to_session(session.name, handover_instruction):
            success_count += 1

    return {"ok": True, "sent": success_count, "total": len(active)}


def cleanup_agents(tmux_session: str, include_done: bool = False) -> dict:
    """Archive terminated (and optionally done) agents."""
    from .session_manager import SessionManager

    sm = SessionManager()
    sessions = sm.list_sessions()
    targets = [
        s for s in sessions
        if s.tmux_session == tmux_session
        and (s.status == "terminated" or (include_done and s.status == "done"))
    ]

    for s in targets:
        sm.delete_session(s.id)

    return {"ok": True, "cleaned": len(targets)}


# ---------------------------------------------------------------------------
# System Control
# ---------------------------------------------------------------------------


def restart_monitor(tmux_session: str) -> dict:
    """Restart monitor daemon."""
    from .monitor_daemon import is_monitor_daemon_running, stop_monitor_daemon

    if is_monitor_daemon_running(tmux_session):
        stop_monitor_daemon(tmux_session)

    import time
    time.sleep(0.5)

    subprocess.Popen(
        [sys.executable, "-m", "overcode.monitor_daemon",
         "--session", tmux_session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True}


def start_supervisor(tmux_session: str) -> dict:
    """Start supervisor daemon."""
    from .supervisor_daemon import is_supervisor_daemon_running

    if is_supervisor_daemon_running(tmux_session):
        raise ControlError("Supervisor daemon already running", status=409)

    subprocess.Popen(
        [sys.executable, "-m", "overcode.supervisor_daemon",
         "--session", tmux_session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True}


def stop_supervisor(tmux_session: str) -> dict:
    """Stop supervisor daemon."""
    from .supervisor_daemon import is_supervisor_daemon_running, stop_supervisor_daemon

    if not is_supervisor_daemon_running(tmux_session):
        raise ControlError("Supervisor daemon not running", status=409)

    if stop_supervisor_daemon(tmux_session):
        return {"ok": True}
    raise ControlError("Failed to stop supervisor daemon", status=500)


def toggle_summarizer(tmux_session: str) -> dict:
    """Toggle AI summarizer on/off.

    Note: This toggles the summarizer config flag. The actual summarizer
    client lifecycle is managed by the TUI, but we can toggle the
    preference so the next TUI startup respects it.
    """
    from .summarizer_client import SummarizerClient

    if not SummarizerClient.is_available():
        raise ControlError("AI Summarizer unavailable - OPENAI_API_KEY not set", status=409)

    # Summarizer state is in-memory in the TUI — we can't toggle it from here.
    # But we can signal via a file that the TUI can pick up.
    # For now, return an error suggesting use via TUI.
    raise ControlError(
        "Summarizer toggle is only available via TUI (press A)",
        status=409,
    )
