"""
Unified new-agent modal with inline-editable text fields and toggles.

Single form that shows all settings at once, pre-filled with sensible
defaults.  The user can review, tweak individual fields, and press 'a'
to launch — locally or on a remote sister.

Field types:
  text   — inline editable (directory, name, wrapper, claude_args)
  toggle — space/enter cycles through options (host, perms, teams, provider)
  select — space/enter cycles through a dynamic list (agent persona)
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, List, Optional

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase

logger = logging.getLogger(__name__)

# ── field descriptors ────────────────────────────────────────────────────

@dataclass
class FormField:
    key: str
    label: str
    type: str               # "text", "toggle", "select"
    value: str = ""
    options: List[str] = dc_field(default_factory=list)   # for toggle/select
    auto: bool = False       # True → value auto-derived (resets on manual edit)


def _unique_name(base: str, existing_names: set[str]) -> str:
    """Derive a unique agent name from *base*, appending 2/3/… on collision."""
    if base not in existing_names:
        return base
    for i in range(2, 100):
        candidate = f"{base}{i}"
        if candidate not in existing_names:
            return candidate
    return f"{base}_new"


# ── modal ────────────────────────────────────────────────────────────────

class NewAgentModal(ModalBase):
    """Keyboard-driven form for launching a new agent.

    Navigation (when not editing):
        j / k / ↑ / ↓   move between fields
        Enter / Space    toggle/cycle (toggle/select) or start editing (text)
        a                launch the agent
        q / Esc          cancel

    Editing a text field:
        printable chars  insert at cursor
        Backspace        delete before cursor
        ← / →            move cursor
        Home / End       jump to start / end
        Enter / Tab      confirm and advance to next field
        Escape           cancel edit (restore previous value)
    """

    # ── messages ─────────────────────────────────────────────────────────

    class LaunchRequested(Message):
        """Emitted when the user presses 'a' to launch."""
        def __init__(
            self,
            host: str,
            is_remote: bool,
            directory: str,
            name: str,
            bypass_permissions: bool,
            agent_teams: bool,
            claude_agent: Optional[str],
            provider: str,
            wrapper: Optional[str],
            extra_claude_args: List[str],
        ) -> None:
            super().__init__()
            self.host = host
            self.is_remote = is_remote
            self.directory = directory
            self.name = name
            self.bypass_permissions = bypass_permissions
            self.agent_teams = agent_teams
            self.claude_agent = claude_agent
            self.provider = provider
            self.wrapper = wrapper
            self.extra_claude_args = extra_claude_args

    class Cancelled(Message):
        pass

    # ── init ─────────────────────────────────────────────────────────────

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields: List[FormField] = []
        self._editing: bool = False
        self._cursor: int = 0
        self._edit_snapshot: str = ""     # value before edit started
        self._existing_names: set[str] = set()
        self._local_hostname: str = ""

    # ── public api ───────────────────────────────────────────────────────

    def show(                               # type: ignore[override]
        self,
        *,
        directory: str,
        defaults: dict,
        agents: List[str],
        existing_names: set[str],
        local_hostname: str = "",
        sister_names: List[str] | None = None,
        wrappers: List[str] | None = None,
        app_ref: Optional[Any] = None,
    ) -> None:
        """Populate the form and display it.

        Args:
            directory: Initial working directory (usually cwd).
            defaults: Dict from get_new_agent_defaults().
            agents: Available Claude agent personas (from scan_agents).
            existing_names: Names already in use (for uniqueness check).
            local_hostname: Name of the local machine.
            sister_names: Names of available remote sisters (omit if none).
            wrappers: Available wrapper names (from list_available_wrappers).
            app_ref: The Textual app, for focus save/restore.
        """
        self._existing_names = existing_names
        self._local_hostname = local_hostname

        base_name = Path(directory).name
        default_name = _unique_name(base_name, existing_names)

        agent_options = ["(none)"] + agents
        wrapper_default = defaults.get("wrapper", "") or ""

        # Build host options: local first, then sisters
        host_options = [local_hostname] if local_hostname else ["local"]
        if sister_names:
            host_options.extend(sister_names)

        self.fields = [
            FormField("host",      "Host",      "toggle", value=host_options[0], options=host_options),
            FormField("directory", "Directory", "text",   value=directory),
            FormField("name",      "Name",      "text",   value=default_name, auto=True),
            FormField("agent",     "Agent",     "select", value="(none)", options=agent_options),
            FormField("perms",     "Perms",     "toggle", value="bypass" if defaults.get("bypass_permissions") else "normal", options=["normal", "bypass"]),
            FormField("teams",     "Teams",     "toggle", value="on" if defaults.get("agent_teams") else "off", options=["off", "on"]),
            FormField("provider",  "Provider",  "toggle", value=defaults.get("provider", "web"), options=["web", "bedrock"]),
            FormField("wrapper",   "Wrapper",   "text",   value=wrapper_default),
            FormField("claude_args", "Claude args", "text", value=""),
        ]

        self._editing = False
        self._cursor = 0
        self._save_focus(app_ref)
        self._show()
        # Start with name field selected (host and directory are usually fine)
        self.selected_index = 2

    # ── helpers ───────────────────────────────────────────────────────────

    def _field(self, key: str) -> FormField:
        return next(f for f in self.fields if f.key == key)

    @property
    def _cur(self) -> FormField:
        return self.fields[self.selected_index]

    @property
    def _is_remote(self) -> bool:
        """True when a remote sister is selected."""
        try:
            host = self._field("host").value
            return host != self._local_hostname and host != "local"
        except StopIteration:
            return False

    def _start_edit(self) -> None:
        f = self._cur
        if f.type != "text":
            return
        self._editing = True
        self._edit_snapshot = f.value
        self._cursor = len(f.value)
        self.refresh()

    def _confirm_edit(self, advance: bool = True) -> None:
        f = self._cur
        self._editing = False
        # If name was manually edited, clear auto flag
        if f.key == "name" and f.value != self._edit_snapshot:
            f.auto = False
        self.refresh()
        if advance:
            self.selected_index = (self.selected_index + 1) % len(self.fields)
            self.refresh()

    def _cancel_edit(self) -> None:
        self._cur.value = self._edit_snapshot
        self._editing = False
        self.refresh()

    def _cycle(self, f: FormField) -> None:
        if not f.options:
            return
        try:
            idx = f.options.index(f.value)
        except ValueError:
            idx = -1
        f.value = f.options[(idx + 1) % len(f.options)]
        # When host changes, adjust directory default
        if f.key == "host":
            self._on_host_changed()
        self.refresh()

    def _on_host_changed(self) -> None:
        """Adjust directory default when host toggles between local/remote."""
        dir_field = self._field("directory")
        if self._is_remote:
            # Switch absolute local paths to "." for remote
            if not dir_field.value or Path(dir_field.value).is_absolute():
                dir_field.value = "."
        else:
            if dir_field.value == ".":
                dir_field.value = str(Path.cwd())

    def _rederive_name(self) -> None:
        """Auto-update name from directory if it hasn't been manually edited."""
        name_field = self._field("name")
        if not name_field.auto:
            return
        dir_field = self._field("directory")
        base = Path(dir_field.value).name if dir_field.value else "agent"
        name_field.value = _unique_name(base, self._existing_names)

    def _launch(self) -> None:
        d = {f.key: f.value for f in self.fields}
        agent = d["agent"] if d["agent"] != "(none)" else None
        wrapper = d["wrapper"].strip() or None
        host = d["host"]
        is_remote = self._is_remote
        raw_args = d.get("claude_args", "").strip()
        if raw_args:
            try:
                shlex.split(raw_args)  # syntax-check (balanced quotes etc)
            except ValueError as e:
                self.notify(f"Invalid Claude args: {e}", severity="error")
                return
            extra_claude_args = [raw_args]
        else:
            extra_claude_args = []
        self.post_message(self.LaunchRequested(
            host=host,
            is_remote=is_remote,
            directory=d["directory"],
            name=d["name"],
            bypass_permissions=(d["perms"] == "bypass"),
            agent_teams=(d["teams"] == "on"),
            claude_agent=agent,
            provider=d["provider"],
            wrapper=wrapper,
            extra_claude_args=extra_claude_args,
        ))
        self._hide()

    def _cancel(self) -> None:
        if self._editing:
            self._cancel_edit()
            return
        self.post_message(self.Cancelled())
        self._hide()

    # ── render ───────────────────────────────────────────────────────────

    LABEL_W = 12  # fixed label column width

    def render(self) -> Text:
        t = Text()
        t.append("New Agent\n", style="bold cyan")
        if self._editing:
            t.append("type to edit  enter:confirm  esc:cancel\n\n", style="dim")
        else:
            t.append("j/k:move  enter:edit  space:toggle  a:launch  q:cancel\n\n", style="dim")

        for i, f in enumerate(self.fields):
            sel = i == self.selected_index
            editing = sel and self._editing

            # Cursor prefix
            t.append("> " if sel else "  ", style="bold cyan" if sel else "")

            # Label
            t.append(f"{f.label:<{self.LABEL_W}}", style="bold" if sel else "dim")

            # Value
            if editing:
                self._render_editable(t, f)
            elif f.type == "text":
                val = f.value or "(none)"
                style = "" if sel else "dim" if not f.value else ""
                t.append(val, style=style)
            elif f.type in ("toggle", "select"):
                self._render_option(t, f, sel)

            t.append("\n")

        return t

    def _render_editable(self, t: Text, f: FormField) -> None:
        """Render a text field in edit mode with a block cursor."""
        val = f.value
        pos = min(self._cursor, len(val))

        # Text before cursor
        if pos > 0:
            t.append(val[:pos])

        # Cursor character (reverse video)
        if pos < len(val):
            t.append(val[pos], style="reverse")
            t.append(val[pos + 1:])
        else:
            t.append(" ", style="reverse")  # cursor at end

    def _render_option(self, t: Text, f: FormField, selected: bool) -> None:
        """Render a toggle/select field showing all options."""
        for j, opt in enumerate(f.options):
            is_active = opt == f.value
            if is_active:
                t.append(f" {opt} ", style="reverse bold" if selected else "reverse")
            else:
                t.append(f" {opt} ", style="dim")
            if j < len(f.options) - 1:
                t.append(" ", style="")

    # ── key handling ─────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        if not self.fields:
            return

        if self._editing:
            self._handle_edit_key(event)
            return

        # Navigation
        if self._navigate(event, len(self.fields)):
            return

        key = event.key
        f = self._cur

        if key in ("enter", "space"):
            if f.type == "text":
                self._start_edit()
            else:
                self._cycle(f)
            event.stop()
        elif key in ("a", "A"):
            self._launch()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _handle_edit_key(self, event: events.Key) -> None:
        key = event.key
        f = self._cur

        if key == "enter" or key == "tab":
            if f.key == "directory":
                self._rederive_name()
            self._confirm_edit(advance=True)
            event.stop()
        elif key == "escape":
            self._cancel_edit()
            event.stop()
        elif key == "backspace":
            if self._cursor > 0:
                f.value = f.value[:self._cursor - 1] + f.value[self._cursor:]
                self._cursor -= 1
            self.refresh()
            event.stop()
        elif key == "delete":
            if self._cursor < len(f.value):
                f.value = f.value[:self._cursor] + f.value[self._cursor + 1:]
            self.refresh()
            event.stop()
        elif key == "left":
            self._cursor = max(0, self._cursor - 1)
            self.refresh()
            event.stop()
        elif key == "right":
            self._cursor = min(len(f.value), self._cursor + 1)
            self.refresh()
            event.stop()
        elif key == "home":
            self._cursor = 0
            self.refresh()
            event.stop()
        elif key == "end":
            self._cursor = len(f.value)
            self.refresh()
            event.stop()
        elif event.character and event.character.isprintable():
            f.value = f.value[:self._cursor] + event.character + f.value[self._cursor:]
            self._cursor += 1
            self.refresh()
            event.stop()
        else:
            # Swallow unrecognised keys so they don't bubble to parent
            event.stop()
