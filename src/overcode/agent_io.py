"""
Talking to a running agent through its tmux window.

``AgentIOMixin`` is the part of ``AgentLauncher`` that sends text, keys and
gestures to an agent, waits for its input prompt, and reads its pane. It
uses the launcher's ``tmux`` (TmuxManager), ``sessions`` (SessionManager),
``_resolve`` and ``backend_for``.
"""

import subprocess
import time
from typing import Optional

from .backends import AgentBackend, get_backend
from .session_manager import Session
from .tmux_utils import (
    get_tmux_pane_content,
    send_text_to_tmux_window,
    tmux_window_target,
)


class _DefaultPromptReadyChars:
    """``AgentLauncher.PROMPT_READY_CHARS``: the default backend's, looked up on use.

    A plain class attribute loaded a backend when this module was imported.
    """

    def __get__(self, obj, owner=None):
        return get_backend().prompt_ready_chars()


class AgentIOMixin:
    """Send to, wait for and read an agent's tmux window (see module docstring)."""

    # Characters that indicate the default backend's input prompt is ready
    PROMPT_READY_CHARS = _DefaultPromptReadyChars()


    def _wait_for_prompt(
        self,
        window_name: str,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
        backend: Optional[AgentBackend] = None,
    ) -> bool:
        """Poll pane content until the agent's input prompt appears.

        Dismisses the backend's startup dialogs on the way (for Claude:
        the workspace trust dialog and the bypass-permissions warning).

        Returns True if prompt detected, False on timeout.
        """
        from .status_patterns import strip_ansi
        from .tmux_utils import _build_tmux_cmd

        backend = backend or get_backend()
        target = tmux_window_target(self.tmux.session_name, window_name)
        tmux_cmd = _build_tmux_cmd()
        prompt_chars = backend.prompt_ready_chars()
        # A backend whose idle prompt is never a bare glyph (hermes) answers
        # through prompt_ready_line; the exact-set match is the default.
        prompt_ready_line = getattr(backend, "prompt_ready_line", None)
        rules = backend.startup_dialog_rules()
        handled: set[str] = set()

        deadline = time.time() + timeout
        while time.time() < deadline:
            content = get_tmux_pane_content(
                self.tmux.session_name, window_name, lines=20
            )
            if content:
                dismissed = False
                for rule in rules:
                    if rule.marker in handled or rule.marker not in content:
                        continue
                    for press in rule.presses:
                        keys = [press.keys, 'Enter'] if press.enter else [press.keys]
                        subprocess.run(tmux_cmd + ['send-keys', '-t', target] + keys, timeout=5)
                        if press.delay_after:
                            time.sleep(press.delay_after)
                    handled.add(rule.marker)
                    time.sleep(rule.settle_seconds)
                    dismissed = True
                    break
                if dismissed:
                    continue

                for line in content.split('\n'):
                    cleaned = strip_ansi(line).strip()
                    if prompt_ready_line is not None:
                        if prompt_ready_line(cleaned):
                            return True
                    elif cleaned in prompt_chars:
                        return True
            time.sleep(poll_interval)
        return False

    def _send_prompt_to_window(
        self,
        window_name: str,
        prompt: str,
        startup_delay: float = 3.0,
        backend: Optional[AgentBackend] = None,
    ) -> bool:
        """Send a prompt to an agent session via tmux load-buffer/paste-buffer.

        Polls for the agent's input prompt before sending. Falls back to
        startup_delay if the prompt is not detected within 30 seconds.
        """
        if self._wait_for_prompt(window_name, backend=backend):
            # Prompt detected — send immediately, no delay needed
            return send_text_to_tmux_window(
                self.tmux.session_name,
                window_name,
                prompt,
                send_enter=True,
                startup_delay=0,
            )
        # Fallback: prompt not detected, use original delay
        return send_text_to_tmux_window(
            self.tmux.session_name,
            window_name,
            prompt,
            send_enter=True,
            startup_delay=startup_delay,
        )

    def send_to_session(self, name: str, text: str, enter: bool = True) -> bool:
        """Send text/keys to a session by name.

        Args:
            name: Name of the session
            text: Text to send (or special key like "Enter", "Escape")
            enter: Whether to press Enter after the text (default: True)

        Returns:
            True if successful, False otherwise
        """
        session = self._resolve(name)
        if session is None:
            print(f"Session '{name}' not found")
            return False

        return self._send_to_resolved_session(session, text, enter)

    def send_to_session_by_id(self, session_id: str, text: str, enter: bool = True) -> bool:
        """Send text/keys to a session by ID.

        Preferred over send_to_session() when the session ID is known,
        since IDs are unique even when local and remote agents share a name.

        Args:
            session_id: Unique session ID
            text: Text to send (or special key like "Enter", "Escape")
            enter: Whether to press Enter after the text (default: True)

        Returns:
            True if successful, False otherwise
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            return False

        return self._send_to_resolved_session(session, text, enter)

    def _send_to_resolved_session(self, session: Session, text: str, enter: bool = True) -> bool:
        """Send text/keys to an already-resolved session.

        Internal helper shared by send_to_session() and send_to_session_by_id().
        """
        # Handle special keys
        special_keys = {
            "enter": "",  # Empty string + Enter = just press Enter
            "escape": "Escape",
            "esc": "Escape",
            "tab": "Tab",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
        }

        # Backend-resolved gestures. "enter"/"escape" are raw keys and stay
        # raw; "approve"/"reject" ask the session's backend what its permission
        # dialog wants, so a supervisor recipe can stay backend-neutral.
        gesture_keys = {
            "approve": "approve_keys",
            "reject": "reject_keys",
        }

        # Check if it's a special key
        text_lower = text.lower().strip()
        success = False
        if text_lower in gesture_keys:
            backend = self.backend_for(session)
            presses = getattr(backend, gesture_keys[text_lower])()
            success = bool(presses)
            for press in presses:
                if not self.tmux.send_keys(
                    session.tmux_window, press.keys, enter=press.enter
                ):
                    success = False
                    break
                if press.delay_after:
                    time.sleep(press.delay_after)
        elif text_lower in special_keys:
            key = special_keys[text_lower]
            if key == "":
                # Just press Enter
                success = self.tmux.send_keys(session.tmux_window, "", enter=True)
            else:
                # Send special key without Enter
                success = self.tmux.send_keys(session.tmux_window, key, enter=False)
        elif '\n' in text:
            # Multi-line text: use load-buffer/paste-buffer so newlines are
            # pasted as a single block instead of being interpreted as Enter
            # keypresses by tmux send-keys (#376)
            success = send_text_to_tmux_window(
                self.tmux.session_name, session.tmux_window, text,
                send_enter=enter,
            )
        else:
            # Single-line regular text
            success = self.tmux.send_keys(session.tmux_window, text, enter=enter)

        # Update last activity on success (steers_count is tracked via supervisor log parsing)
        if success:
            self.sessions.update_stats(
                session.id,
                last_activity=time.strftime("%Y-%m-%dT%H:%M:%S")
            )

        return success

    def get_session_output(self, name: str, lines: int = 50) -> Optional[str]:
        """Get recent output from a session.

        Args:
            name: Name of the session
            lines: Number of lines to capture (default: 50)

        Returns:
            The captured output, or None if session not found
        """
        session = self._resolve(name)
        if session is None:
            print(f"Session '{name}' not found")
            return None

        try:
            from .tmux_utils import _build_tmux_cmd

            result = subprocess.run(
                [
                    *_build_tmux_cmd(), "capture-pane",
                    "-t", tmux_window_target(self.tmux.session_name, session.tmux_window),
                    "-p",  # Print to stdout
                    "-S", f"-{lines}",  # Capture last N lines
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.rstrip()
            return None
        except subprocess.SubprocessError:
            return None
