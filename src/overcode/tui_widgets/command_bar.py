"""
Command bar widget for TUI.

Inline command bar for sending instructions to agents.
"""

from typing import Optional, Tuple

from textual.widgets import Static, Label, Input, TextArea
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.app import ComposeResult
from textual.message import Message
from textual import events


def get_mode_label_and_placeholder(mode: str, target_session: Optional[str]) -> Tuple[str, str]:
    """Get the label text and placeholder for a given command bar mode.

    Pure function — no side effects, fully testable.

    Args:
        mode: The command bar mode (send, standing_orders, fork_name, etc.)
        target_session: The currently targeted session name, or None

    Returns:
        Tuple of (label_text, placeholder_text)
    """
    if mode == "fork_name":
        return "[Fork: Name] ", "Enter name for forked agent (or Enter to accept default)..."
    elif mode == "standing_orders":
        prefix = f"[{target_session} Standing Orders] " if target_session else "[Standing Orders] "
        return prefix, "Enter standing orders (or empty to clear)..."
    elif mode == "value":
        prefix = f"[{target_session} Value] " if target_session else "[Value] "
        return prefix, "Enter priority value (1000 = normal, higher = more important)..."
    elif mode == "cost_budget":
        prefix = f"[{target_session} Budget] " if target_session else "[Budget] "
        return prefix, "Enter $ budget (e.g., 5.00) or 0 to clear..."
    elif mode == "annotation":
        prefix = f"[{target_session} Annotation] " if target_session else "[Annotation] "
        return prefix, "Enter human annotation (or empty to clear)..."
    elif mode == "heartbeat_freq":
        prefix = f"[{target_session} Heartbeat: Frequency] " if target_session else "[Heartbeat: Frequency] "
        return prefix, "Enter interval (e.g., 300, 5m, 1h) or empty to disable..."
    elif mode == "heartbeat_instruction":
        prefix = f"[{target_session} Heartbeat: Instruction] " if target_session else "[Heartbeat: Instruction] "
        return prefix, "Enter instruction to send at each heartbeat..."
    elif target_session:
        return f"[{target_session}] ", "Type instruction (Enter to send)..."
    else:
        return "[no session] ", "Type instruction (Enter to send)..."


class CommandBar(Static):
    """Inline command bar for sending instructions to agents.

    Supports single-line (Input) and multi-line (TextArea) modes.
    Toggle with Ctrl+E. Send with Enter (single) or Ctrl+S / Ctrl+Enter (multi).
    Use Ctrl+O to set as standing order instead of sending.

    Modes:
    - "send": Default mode for sending instructions to an agent
    - "standing_orders": Mode for editing standing orders for an agent
    Key handling is done via on_key() since Input/TextArea consume most keys.
    """

    expanded = reactive(False)  # Toggle single/multi-line mode
    target_session: Optional[str] = None
    target_session_id: Optional[str] = None
    mode: str = "send"  # "send", "standing_orders", "heartbeat_freq", "heartbeat_instruction", etc.
    heartbeat_freq: Optional[int] = None  # Store frequency between heartbeat steps (#171)
    fork_source_session: Optional[object] = None  # Store source session for fork flow (#347)

    class SendRequested(Message):
        """Message sent when user wants to send text to a session."""
        def __init__(self, session_name: str, text: str, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.text = text
            self.session_id = session_id

    class StandingOrderRequested(Message):
        """Message sent when user wants to set a standing order."""
        def __init__(self, session_name: str, text: str, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.text = text
            self.session_id = session_id

    class ValueUpdated(Message):
        """Message sent when user updates agent value (#61)."""
        def __init__(self, session_name: str, value: int, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.value = value
            self.session_id = session_id

    class AnnotationUpdated(Message):
        """Message sent when user updates human annotation (#74)."""
        def __init__(self, session_name: str, annotation: str, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.annotation = annotation
            self.session_id = session_id

    class HeartbeatUpdated(Message):
        """Message sent when user configures heartbeat (#171)."""
        def __init__(self, session_name: str, enabled: bool, frequency: int, instruction: str, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.enabled = enabled
            self.frequency = frequency
            self.instruction = instruction
            self.session_id = session_id

    class BudgetUpdated(Message):
        """Message sent when user updates cost budget (#173)."""
        def __init__(self, session_name: str, budget_usd: float, session_id: str = ""):
            super().__init__()
            self.session_name = session_name
            self.budget_usd = budget_usd
            self.session_id = session_id

    class ForkRequested(Message):
        """Message sent when user wants to fork an agent (#347)."""
        def __init__(self, source_session, fork_name: str):
            super().__init__()
            self.source_session = source_session
            self.fork_name = fork_name

    class ClearRequested(Message):
        """Message sent when user clears the command bar."""
        pass

    def compose(self) -> ComposeResult:
        """Create command bar widgets."""
        with Horizontal(id="cmd-bar-container"):
            yield Label("", id="target-label")
            yield Input(id="cmd-input", placeholder="Type instruction (Enter to send)...", disabled=True)
            yield TextArea(id="cmd-textarea", classes="hidden", disabled=True)
            yield Label("[^E]", id="expand-hint")

    def on_mount(self) -> None:
        """Initialize command bar state."""
        self._update_target_label()
        # Ensure widgets start disabled to prevent auto-focus
        self.query_one("#cmd-input", Input).disabled = True
        self.query_one("#cmd-textarea", TextArea).disabled = True

    def _update_target_label(self) -> None:
        """Update the target session label based on mode."""
        label = self.query_one("#target-label", Label)
        input_widget = self.query_one("#cmd-input", Input)
        label_text, placeholder = get_mode_label_and_placeholder(self.mode, self.target_session)
        label.update(label_text)
        input_widget.placeholder = placeholder

    def set_target(self, session_name: Optional[str], session_id: Optional[str] = None) -> None:
        """Set the target session for commands."""
        self.target_session = session_name
        self.target_session_id = session_id
        self.mode = "send"  # Reset to send mode when target changes
        self._update_target_label()

    def set_mode(self, mode: str) -> None:
        """Set the command bar mode ('send' or 'new_agent')."""
        self.mode = mode
        self._update_target_label()

    def watch_expanded(self, expanded: bool) -> None:
        """Toggle between single-line and multi-line mode."""
        input_widget = self.query_one("#cmd-input", Input)
        textarea = self.query_one("#cmd-textarea", TextArea)

        if expanded:
            # Switch to multi-line
            input_widget.add_class("hidden")
            input_widget.disabled = True
            textarea.remove_class("hidden")
            textarea.disabled = False
            # Transfer content
            textarea.text = input_widget.value
            input_widget.value = ""
            textarea.focus()
        else:
            # Switch to single-line
            textarea.add_class("hidden")
            textarea.disabled = True
            input_widget.remove_class("hidden")
            input_widget.disabled = False
            # Transfer content (first line only for single-line)
            if textarea.text:
                first_line = textarea.text.split('\n')[0]
                input_widget.value = first_line
            textarea.text = ""
            input_widget.focus()

    def on_key(self, event: events.Key) -> None:
        """Handle key events for command bar shortcuts."""
        if event.key == "ctrl+e":
            self.action_toggle_expand()
            event.stop()
        elif event.key == "ctrl+o":
            self.action_set_standing_order()
            event.stop()
        elif event.key == "escape":
            self.action_clear_and_unfocus()
            event.stop()
        elif event.key in ("ctrl+enter", "ctrl+s") and self.expanded:
            self.action_send_multiline()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in single-line mode."""
        if event.input.id == "cmd-input":
            text = event.value.strip()

            if self.mode == "fork_name":
                # Fork agent: name entered, create the fork (#347)
                fork_name = text if text else event.input.value.strip()
                if not fork_name:
                    self.app.notify("Fork name required", severity="error")
                    return
                self.post_message(self.ForkRequested(self.fork_source_session, fork_name))
                self.fork_source_session = None
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "standing_orders":
                # Set standing orders (empty string clears them)
                self._set_standing_order(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "value":
                # Set agent value (#61)
                self._set_value(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "cost_budget":
                # Set cost budget (#173)
                self._set_cost_budget(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "annotation":
                # Set human annotation (empty string clears it)
                self._set_annotation(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "heartbeat_freq":
                # Handle frequency input for heartbeat configuration (#171)
                self._handle_heartbeat_freq(text)
                event.input.value = ""
                return
            elif self.mode == "heartbeat_instruction":
                # Handle instruction input for heartbeat configuration (#171)
                self._handle_heartbeat_instruction(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return

            # Default "send" mode
            if not text:
                return
            self._send_message(text)
            event.input.value = ""
            self.action_clear_and_unfocus()

    def _send_message(self, text: str) -> None:
        """Send message to target session."""
        if not self.target_session or not text.strip():
            return
        self.post_message(self.SendRequested(self.target_session, text.strip(), session_id=self.target_session_id or ""))

    def _get_unique_agent_name(self, base_name: str) -> str:
        """Get a unique agent name by incrementing suffix if needed (#131)."""
        if not self.app.session_manager.get_session_by_name(base_name):
            return base_name
        suffix = 2
        while suffix < 100:
            candidate = f"{base_name}{suffix}"
            if not self.app.session_manager.get_session_by_name(candidate):
                return candidate
            suffix += 1
        return f"{base_name}_{suffix}"

    def _set_standing_order(self, text: str) -> None:
        """Set text as standing order (empty string clears orders)."""
        if not self.target_session:
            return
        self.post_message(self.StandingOrderRequested(self.target_session, text.strip(), session_id=self.target_session_id or ""))

    def _set_value(self, text: str) -> None:
        """Set agent value (#61)."""
        if not self.target_session:
            return
        try:
            value = int(text.strip()) if text.strip() else 1000
            if value < 0 or value > 9999:
                self.app.notify("Value must be between 0 and 9999", severity="error")
                return
            self.post_message(self.ValueUpdated(self.target_session, value, session_id=self.target_session_id or ""))
        except ValueError:
            # Invalid input, notify user but don't crash
            self.app.notify("Invalid value - please enter a number", severity="error")

    def _set_cost_budget(self, text: str) -> None:
        """Set cost budget (#173)."""
        if not self.target_session:
            return
        try:
            cleaned = text.strip().lstrip('$')
            budget = float(cleaned) if cleaned else 0.0
            if budget < 0:
                self.app.notify("Budget cannot be negative", severity="error")
                return
            self.post_message(self.BudgetUpdated(self.target_session, budget, session_id=self.target_session_id or ""))
        except ValueError:
            self.app.notify("Invalid budget - enter a dollar amount (e.g., 5.00)", severity="error")

    def _set_annotation(self, text: str) -> None:
        """Set human annotation (empty string clears it) (#74)."""
        if not self.target_session:
            return
        self.post_message(self.AnnotationUpdated(self.target_session, text.strip(), session_id=self.target_session_id or ""))

    def action_toggle_expand(self) -> None:
        """Toggle between single and multi-line mode."""
        self.expanded = not self.expanded

    def action_send_multiline(self) -> None:
        """Send content from multi-line textarea."""
        textarea = self.query_one("#cmd-textarea", TextArea)
        self._send_message(textarea.text)
        textarea.text = ""
        self.action_clear_and_unfocus()

    def action_set_standing_order(self) -> None:
        """Set current content as standing order."""
        if self.expanded:
            textarea = self.query_one("#cmd-textarea", TextArea)
            self._set_standing_order(textarea.text)
            textarea.text = ""
        else:
            input_widget = self.query_one("#cmd-input", Input)
            self._set_standing_order(input_widget.value)
            input_widget.value = ""

    def action_clear_and_unfocus(self) -> None:
        """Clear input and unfocus command bar."""
        if self.expanded:
            textarea = self.query_one("#cmd-textarea", TextArea)
            textarea.text = ""
            self.expanded = False  # Reset to single-line mode
        else:
            input_widget = self.query_one("#cmd-input", Input)
            input_widget.value = ""
        # Reset mode and state
        self.mode = "send"
        self.fork_source_session = None  # Reset fork state (#347)
        self.heartbeat_freq = None  # Reset heartbeat state (#171)
        self._update_target_label()
        # Let parent handle unfocus
        self.post_message(self.ClearRequested())

    def focus_input(self) -> None:
        """Focus the command bar input and enable it."""
        input_widget = self.query_one("#cmd-input", Input)
        input_widget.disabled = False
        input_widget.focus()

    def _find_target_session(self):
        """Find the target session by ID from the app's session list."""
        if not self.target_session_id:
            return None
        for s in self.app.sessions:
            if s.id == self.target_session_id:
                return s
        return None

    def _parse_duration(self, text: str) -> Optional[int]:
        """Parse duration string like '5m', '1h', '300' into seconds (#171)."""
        text = text.strip().lower()
        if not text:
            return None
        try:
            if text.endswith('s'):
                return int(text[:-1])
            elif text.endswith('m'):
                return int(text[:-1]) * 60
            elif text.endswith('h'):
                return int(text[:-1]) * 3600
            else:
                return int(text)
        except ValueError:
            return None

    def _handle_heartbeat_freq(self, text: str) -> None:
        """Handle frequency input for heartbeat configuration (#171)."""
        if not text.strip() or text.lower().strip() in ('off', 'disable', '0', 'no', 'false'):
            # Disable heartbeat
            if self.target_session:
                self.post_message(self.HeartbeatUpdated(
                    self.target_session, enabled=False, frequency=0, instruction="",
                    session_id=self.target_session_id or "",
                ))
            self.action_clear_and_unfocus()
            return

        freq = self._parse_duration(text)
        if freq is None:
            self.app.notify("Invalid format. Use: 300, 5m, or 1h", severity="error")
            return
        if freq < 30:
            self.app.notify("Minimum heartbeat interval is 30 seconds", severity="error")
            return

        self.heartbeat_freq = freq
        self.mode = "heartbeat_instruction"
        self._update_target_label()

        # Pre-fill with existing heartbeat instruction if available
        if self.target_session:
            session = self._find_target_session()
            if session and session.heartbeat_instruction:
                input_widget = self.query_one("#cmd-input", Input)
                input_widget.value = session.heartbeat_instruction

    def _handle_heartbeat_instruction(self, text: str) -> None:
        """Handle instruction input for heartbeat configuration (#171)."""
        if not self.target_session:
            return

        instruction = text.strip()
        # If empty, keep the existing instruction (user just hit Enter to confirm)
        if not instruction:
            session = self._find_target_session()
            if session and session.heartbeat_instruction:
                instruction = session.heartbeat_instruction
            else:
                self.app.notify("Heartbeat instruction cannot be empty", severity="error")
                return

        self.post_message(self.HeartbeatUpdated(
            self.target_session,
            enabled=True,
            frequency=self.heartbeat_freq or 300,
            instruction=instruction,
            session_id=self.target_session_id or "",
        ))
        self.heartbeat_freq = None
