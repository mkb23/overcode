"""
Command bar widget for TUI.

Inline command bar for sending instructions to agents.
"""

from typing import Optional

from textual.widgets import Static, Label, Input, TextArea
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.app import ComposeResult
from textual.message import Message
from textual import events


class CommandBar(Static):
    """Inline command bar for sending instructions to agents.

    Supports single-line (Input) and multi-line (TextArea) modes.
    Toggle with Ctrl+E. Send with Enter (single) or Ctrl+Enter (multi).
    Use Ctrl+O to set as standing order instead of sending.

    Modes:
    - "send": Default mode for sending instructions to an agent
    - "standing_orders": Mode for editing standing orders for an agent
    - "new_agent_dir": First step of new agent creation - enter working directory
    - "new_agent_name": Second step of new agent creation - enter agent name
    - "new_agent_perms": Third step of new agent creation - choose permission mode

    Key handling is done via on_key() since Input/TextArea consume most keys.
    """

    expanded = reactive(False)  # Toggle single/multi-line mode
    target_session: Optional[str] = None
    mode: str = "send"  # "send", "standing_orders", "new_agent_*", "heartbeat_freq", "heartbeat_instruction", etc.
    new_agent_dir: Optional[str] = None  # Store directory between steps
    new_agent_name: Optional[str] = None  # Store name between steps
    heartbeat_freq: Optional[int] = None  # Store frequency between heartbeat steps (#171)

    class SendRequested(Message):
        """Message sent when user wants to send text to a session."""
        def __init__(self, session_name: str, text: str):
            super().__init__()
            self.session_name = session_name
            self.text = text

    class StandingOrderRequested(Message):
        """Message sent when user wants to set a standing order."""
        def __init__(self, session_name: str, text: str):
            super().__init__()
            self.session_name = session_name
            self.text = text

    class NewAgentRequested(Message):
        """Message sent when user wants to create a new agent."""
        def __init__(self, agent_name: str, directory: Optional[str] = None, bypass_permissions: bool = False):
            super().__init__()
            self.agent_name = agent_name
            self.directory = directory
            self.bypass_permissions = bypass_permissions

    class ValueUpdated(Message):
        """Message sent when user updates agent value (#61)."""
        def __init__(self, session_name: str, value: int):
            super().__init__()
            self.session_name = session_name
            self.value = value

    class AnnotationUpdated(Message):
        """Message sent when user updates human annotation (#74)."""
        def __init__(self, session_name: str, annotation: str):
            super().__init__()
            self.session_name = session_name
            self.annotation = annotation

    class HeartbeatUpdated(Message):
        """Message sent when user configures heartbeat (#171)."""
        def __init__(self, session_name: str, enabled: bool, frequency: int, instruction: str):
            super().__init__()
            self.session_name = session_name
            self.enabled = enabled
            self.frequency = frequency
            self.instruction = instruction

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

        if self.mode == "new_agent_dir":
            label.update("[New Agent: Directory] ")
            input_widget.placeholder = "Enter working directory path..."
        elif self.mode == "new_agent_name":
            label.update("[New Agent: Name] ")
            input_widget.placeholder = "Enter agent name (or Enter to accept default)..."
        elif self.mode == "new_agent_perms":
            label.update("[New Agent: Permissions] ")
            input_widget.placeholder = "Type 'bypass' for --dangerously-skip-permissions, or Enter for normal..."
        elif self.mode == "standing_orders":
            if self.target_session:
                label.update(f"[{self.target_session} Standing Orders] ")
            else:
                label.update("[Standing Orders] ")
            input_widget.placeholder = "Enter standing orders (or empty to clear)..."
        elif self.mode == "value":
            if self.target_session:
                label.update(f"[{self.target_session} Value] ")
            else:
                label.update("[Value] ")
            input_widget.placeholder = "Enter priority value (1000 = normal, higher = more important)..."
        elif self.mode == "annotation":
            if self.target_session:
                label.update(f"[{self.target_session} Annotation] ")
            else:
                label.update("[Annotation] ")
            input_widget.placeholder = "Enter human annotation (or empty to clear)..."
        elif self.mode == "heartbeat_freq":
            if self.target_session:
                label.update(f"[{self.target_session} Heartbeat: Frequency] ")
            else:
                label.update("[Heartbeat: Frequency] ")
            input_widget.placeholder = "Enter interval (e.g., 300, 5m, 1h) or 'off' to disable..."
        elif self.mode == "heartbeat_instruction":
            if self.target_session:
                label.update(f"[{self.target_session} Heartbeat: Instruction] ")
            else:
                label.update("[Heartbeat: Instruction] ")
            input_widget.placeholder = "Enter instruction to send at each heartbeat..."
        elif self.target_session:
            label.update(f"[{self.target_session}] ")
            input_widget.placeholder = "Type instruction (Enter to send)..."
        else:
            label.update("[no session] ")
            input_widget.placeholder = "Type instruction (Enter to send)..."

    def set_target(self, session_name: Optional[str]) -> None:
        """Set the target session for commands."""
        self.target_session = session_name
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
        elif event.key == "ctrl+enter" and self.expanded:
            self.action_send_multiline()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in single-line mode."""
        if event.input.id == "cmd-input":
            text = event.value.strip()

            if self.mode == "new_agent_dir":
                # Step 1: Directory entered, validate and move to name step
                # Note: _handle_new_agent_dir sets input value to default name, don't clear it
                self._handle_new_agent_dir(text if text else None)
                return
            elif self.mode == "new_agent_name":
                # Step 2: Name entered (or default accepted), move to permissions step
                # If empty, use the pre-filled default
                name = text if text else event.input.value.strip()
                if not name:
                    # Derive from directory as fallback
                    from pathlib import Path
                    name = Path(self.new_agent_dir).name if self.new_agent_dir else "agent"
                self._handle_new_agent_name(name)
                event.input.value = ""
                return
            elif self.mode == "new_agent_perms":
                # Step 3: Permissions chosen, create agent
                bypass = text.lower().strip() in ("bypass", "y", "yes", "!")
                self._create_new_agent(self.new_agent_name, bypass)
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
        self.post_message(self.SendRequested(self.target_session, text.strip()))

    def _handle_new_agent_dir(self, directory: Optional[str]) -> None:
        """Handle directory input for new agent creation.

        Validates directory and transitions to name input step.
        """
        from pathlib import Path

        # Expand ~ and resolve path
        if directory:
            dir_path = Path(directory).expanduser().resolve()
            if not dir_path.exists():
                # Create the directory
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    self.app.notify(f"Created directory: {dir_path}", severity="information")
                except OSError as e:
                    self.app.notify(f"Failed to create directory: {e}", severity="error")
                    return
            if not dir_path.is_dir():
                self.app.notify(f"Not a directory: {dir_path}", severity="error")
                return
            self.new_agent_dir = str(dir_path)
        else:
            # Use current working directory if none specified
            self.new_agent_dir = str(Path.cwd())

        # Derive default agent name from directory basename (#131)
        # If an agent with that name exists, increment (foo -> foo2 -> foo3)
        base_name = Path(self.new_agent_dir).name
        default_name = self._get_unique_agent_name(base_name)

        # Transition to name step
        self.mode = "new_agent_name"
        self._update_target_label()

        # Pre-fill the input with the default name
        input_widget = self.query_one("#cmd-input", Input)
        input_widget.value = default_name

    def _get_unique_agent_name(self, base_name: str) -> str:
        """Get a unique agent name by incrementing suffix if needed (#131).

        Args:
            base_name: The base name to start with (e.g., directory name)

        Returns:
            A unique name: base_name if available, else base_name2, base_name3, etc.
        """
        # Check if base name is available
        if not self.app.session_manager.get_session_by_name(base_name):
            return base_name

        # Try incrementing suffix until we find an unused name
        suffix = 2
        while suffix < 100:  # Reasonable limit
            candidate = f"{base_name}{suffix}"
            if not self.app.session_manager.get_session_by_name(candidate):
                return candidate
            suffix += 1

        # Fallback (very unlikely to reach)
        return f"{base_name}_{suffix}"

    def _handle_new_agent_name(self, name: str) -> None:
        """Handle name input for new agent creation.

        Stores the name and transitions to permissions step.
        """
        self.new_agent_name = name

        # Transition to permissions step
        self.mode = "new_agent_perms"
        self._update_target_label()

    def _create_new_agent(self, name: str, bypass_permissions: bool = False) -> None:
        """Create a new agent with the given name, directory, and permission mode."""
        self.post_message(self.NewAgentRequested(name, self.new_agent_dir, bypass_permissions))
        # Reset state
        self.new_agent_dir = None
        self.new_agent_name = None
        self.mode = "send"
        self._update_target_label()

    def _set_standing_order(self, text: str) -> None:
        """Set text as standing order (empty string clears orders)."""
        if not self.target_session:
            return
        self.post_message(self.StandingOrderRequested(self.target_session, text.strip()))

    def _set_value(self, text: str) -> None:
        """Set agent value (#61)."""
        if not self.target_session:
            return
        try:
            value = int(text.strip()) if text.strip() else 1000
            if value < 0 or value > 9999:
                self.app.notify("Value must be between 0 and 9999", severity="error")
                return
            self.post_message(self.ValueUpdated(self.target_session, value))
        except ValueError:
            # Invalid input, notify user but don't crash
            self.app.notify("Invalid value - please enter a number", severity="error")

    def _set_annotation(self, text: str) -> None:
        """Set human annotation (empty string clears it) (#74)."""
        if not self.target_session:
            return
        self.post_message(self.AnnotationUpdated(self.target_session, text.strip()))

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
        else:
            input_widget = self.query_one("#cmd-input", Input)
            input_widget.value = ""
        # Reset mode and state
        self.mode = "send"
        self.new_agent_dir = None
        self.new_agent_name = None
        self.heartbeat_freq = None  # Reset heartbeat state (#171)
        self._update_target_label()
        # Let parent handle unfocus
        self.post_message(self.ClearRequested())

    def focus_input(self) -> None:
        """Focus the command bar input and enable it."""
        input_widget = self.query_one("#cmd-input", Input)
        input_widget.disabled = False
        input_widget.focus()

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
        if text.lower().strip() in ('off', 'disable', '0', 'no', 'false'):
            # Disable heartbeat
            if self.target_session:
                self.post_message(self.HeartbeatUpdated(
                    self.target_session, enabled=False, frequency=0, instruction=""
                ))
            self.action_clear_and_unfocus()
            return

        freq = self._parse_duration(text) if text else 300  # Default 5 min
        if freq is None:
            self.app.notify("Invalid format. Use: 300, 5m, or 1h", severity="error")
            return
        if freq < 30:
            self.app.notify("Minimum heartbeat interval is 30 seconds", severity="error")
            return

        self.heartbeat_freq = freq
        self.mode = "heartbeat_instruction"
        self._update_target_label()

    def _handle_heartbeat_instruction(self, text: str) -> None:
        """Handle instruction input for heartbeat configuration (#171)."""
        if not self.target_session:
            return
        if not text.strip():
            self.app.notify("Heartbeat instruction cannot be empty", severity="error")
            return

        self.post_message(self.HeartbeatUpdated(
            self.target_session,
            enabled=True,
            frequency=self.heartbeat_freq or 300,
            instruction=text.strip()
        ))
        self.heartbeat_freq = None
