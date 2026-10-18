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
    type_text_to_tmux_window,
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
        from .tmux_utils import _build_tmux_cmd

        backend = backend or get_backend()
        target = tmux_window_target(self.tmux.session_name, window_name)
        tmux_cmd = _build_tmux_cmd()
        rules = backend.startup_dialog_rules()
        handled: set[str] = set()
        settle = backend.prompt_settle_seconds

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
                    completed = True
                    for press in rule.presses:
                        if press.wait_for and not self._pane_shows(
                            window_name, press.wait_for, timeout=3.0
                        ):
                            # Not where we meant to be (an early key was
                            # dropped): send nothing more, try the dialog again
                            completed = False
                            break
                        keys = [press.keys, 'Enter'] if press.enter else [press.keys]
                        subprocess.run(tmux_cmd + ['send-keys', '-t', target] + keys, timeout=5)
                        if press.delay_after:
                            time.sleep(press.delay_after)
                    if completed:
                        handled.add(rule.marker)
                        handled.update(rule.supersedes)
                    time.sleep(rule.settle_seconds)
                    dismissed = True
                    break
                if dismissed:
                    continue

                if self._shows_prompt(content, backend):
                    if not settle:
                        return True
                    # The prompt can be drawn before a dialog over it (codex
                    # 0.156 paints its input line, then the trust dialog):
                    # look again before typing into what may be a menu
                    time.sleep(settle)
                    again = get_tmux_pane_content(
                        self.tmux.session_name, window_name, lines=20
                    ) or ""
                    if not any(
                        r.marker not in handled and r.marker in again for r in rules
                    ) and self._shows_prompt(again, backend):
                        return True
                    continue
            time.sleep(poll_interval)
        return False

    @staticmethod
    def _shows_prompt(content: str, backend: AgentBackend) -> bool:
        from .status_patterns import strip_ansi

        return any(backend.prompt_ready_line(strip_ansi(line).strip())
                   for line in content.split('\n'))

    def _pane_shows(self, window_name: str, text: str, timeout: float) -> bool:
        """Whether ``text`` appears in the window within ``timeout`` seconds."""
        deadline = time.time() + timeout
        while True:
            content = get_tmux_pane_content(self.tmux.session_name, window_name, lines=20)
            if content and text in content:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def _prompt_landed(self, window_name: str, prompt: str, timeout: float) -> bool:
        """Whether any sign of ``prompt`` shows in the window within ``timeout``.

        Its first or last line, or codex's "[Pasted Content" placeholder,
        anywhere in enough scrollback to hold the whole prompt: a long
        prompt's first line has scrolled away by the time it is drawn. Runs
        of whitespace match whatever the pane shows for them (tabs come
        back as spaces, the agent wraps lines).
        """
        def squash(text: str) -> str:
            return " ".join(text.split())

        lines = [squash(line) for line in prompt.splitlines() if line.strip()]
        if not lines:
            return True
        needles = {lines[0][:40], lines[-1][-40:], "[Pasted Content"}
        # Every prompt line wrapped at a narrow 40 columns, plus the screen
        depth = 40 + sum(len(line) // 40 + 1 for line in prompt.splitlines())
        deadline = time.time() + timeout
        while True:
            content = get_tmux_pane_content(self.tmux.session_name, window_name, lines=depth)
            if content:
                shown = squash(content)
                if any(needle in shown for needle in needles):
                    return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def _prompt_unsent(self, window_name: str, prompt: str,
                       backend: AgentBackend, timeout: float) -> bool:
        """Whether ``prompt`` still sits on the input line after ``timeout``.

        The input line is the pane's last line the backend calls ready (a
        resize leaves stale copies above it); unsent, it starts with the
        prompt's first words. Not drawn yet is not sent: False early only
        once the prompt has shown there and gone.
        """
        from .status_patterns import strip_ansi

        first = next((line for line in prompt.splitlines() if line.strip()), "")
        needle = " ".join(first.split())[:30]
        if not needle:
            return False
        seen = False
        deadline = time.time() + timeout
        while True:
            content = get_tmux_pane_content(self.tmux.session_name, window_name, lines=20) or ""
            ready = [line for line in (strip_ansi(raw).strip() for raw in content.split('\n'))
                     if backend.prompt_ready_line(line)]
            unsent = bool(ready) and needle in " ".join(ready[-1].split())
            if seen and not unsent:
                return False
            seen = seen or unsent
            if time.time() >= deadline:
                return unsent
            time.sleep(0.2)

    def _send_prompt_to_window(
        self,
        window_name: str,
        prompt: str,
        startup_delay: float = 3.0,
        backend: Optional[AgentBackend] = None,
    ) -> bool:
        """Send a prompt to an agent once its input line is up.

        Pasted with tmux load-buffer/paste-buffer, or typed for a backend
        that sets ``type_multiline_text`` (Claude hands a long paste to the
        model as <pasted_content>, not to be obeyed). Polls for the agent's
        input prompt before sending. Falls back to startup_delay if the
        prompt is not detected within 30 seconds.
        """
        backend = backend or get_backend()

        def send(delay: float) -> bool:
            if backend.type_multiline_text:
                if delay > 0:
                    time.sleep(delay)
                return type_text_to_tmux_window(
                    self.tmux.session_name, window_name, prompt, send_enter=True)
            return send_text_to_tmux_window(
                self.tmux.session_name, window_name, prompt,
                send_enter=True, startup_delay=delay,
            )

        if self._wait_for_prompt(window_name, backend=backend):
            # Prompt detected — send immediately, no delay needed
            sent = send(0)
            # codex 0.156 draws its input line a few seconds before it reads
            # input after the trust dialog, and drops a paste in between:
            # such a backend has the prompt looked for on screen, and sent
            # again only when no sign of it shows
            if sent and prompt.strip() and backend.confirm_prompt_delivery:
                for _ in range(3):
                    if self._prompt_landed(window_name, prompt, timeout=3.0):
                        break
                    sent = send(0)
            # hermes can read the Enter as a line break (see its backend):
            # Enter again while the prompt has not left the input line
            if sent and prompt.strip() and backend.confirm_prompt_submit:
                for _ in range(3):
                    if not self._prompt_unsent(window_name, prompt, backend, timeout=2.0):
                        break
                    self.tmux.send_keys(window_name, "", enter=True)
            return sent
        # Fallback: prompt not detected, use original delay
        return send(startup_delay)

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
        Sent in the agent's own tmux session: agents are found fleet-wide,
        and this launcher's may not be theirs (the CLI's --session default).
        """
        owner = self.for_agent(session)
        if owner is not self:
            return owner._send_to_resolved_session(session, text, enter)

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
        # Typed, a CR is Return and would submit part of the message: a
        # lone CR line end is a line break like CRLF and LF
        lines = text.replace('\r\n', '\n').replace('\r', '\n')
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
                    self.sleep(press.delay_after)
        elif text_lower in special_keys:
            key = special_keys[text_lower]
            if key == "":
                # Just press Enter
                success = self.tmux.send_keys(session.tmux_window, "", enter=True)
            else:
                # Send special key without Enter
                success = self.tmux.send_keys(session.tmux_window, key, enter=False)
        elif '\n' in lines and self.backend_for(session).type_multiline_text:
            # Claude: a long paste reaches the model as <pasted_content>,
            # not to be obeyed; typed, it is the user's message
            success = type_text_to_tmux_window(
                self.tmux.session_name, session.tmux_window, lines,
                send_enter=enter,
            )
        elif '\n' in lines:
            # Multi-line text: use load-buffer/paste-buffer so newlines are
            # pasted as a single block instead of being interpreted as Enter
            # keypresses by tmux send-keys (#376)
            success = send_text_to_tmux_window(
                self.tmux.session_name, session.tmux_window, lines,
                send_enter=enter,
            )
        else:
            # Single-line regular text. Typed, a tab is the Tab key
            success = self.tmux.send_keys(
                session.tmux_window, lines.expandtabs(4), enter=enter)

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
        tmux_session = self.for_agent(session).tmux.session_name  # its own, not ours

        try:
            from .tmux_utils import _build_tmux_cmd

            result = subprocess.run(
                [
                    *_build_tmux_cmd(), "capture-pane",
                    "-t", tmux_window_target(tmux_session, session.tmux_window),
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
