"""
Sister management modal for TUI.

Shows sister health, daemon status, and allows toggling visibility
and restarting remote daemons.
"""

from typing import Dict, List, Optional, Any, Set

from textual.message import Message
from textual import events
from rich.text import Text

from . import dialog_style as ds
from .modal_base import ModalBase


class SisterSelectionModal(ModalBase):
    """Modal dialog for managing sister instances.

    Navigate with j/k, toggle visibility with space/enter.
    Press r to restart daemon on selected sister.
    Press a to apply, q/Esc to cancel.
    """

    class SelectionChanged(Message):
        """Message sent when sister selection is applied."""

        def __init__(self, disabled_sisters: Set[str]) -> None:
            super().__init__()
            self.disabled_sisters = disabled_sisters

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    class RestartDaemon(Message):
        """Message sent to request daemon restart on a sister."""

        def __init__(self, sister_name: str, sister_url: str, api_key: str) -> None:
            super().__init__()
            self.sister_name = sister_name
            self.sister_url = sister_url
            self.api_key = api_key

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sisters: List[Dict[str, Any]] = []
        self._disabled: Set[str] = set()
        self._original_disabled: Set[str] = set()

    TITLE = "Sister instances"
    WIDTH = 92

    def hints(self) -> str:
        return ds.hints(("space", "show/hide"), ("r", "restart daemon"), ("a", "apply"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")

        if not self._sisters:
            text.append_text(ds.finish(Text("  No sisters configured.", style=f"italic {ds.MUTED}"), w))
            text.append("\n")
            text.append_text(ds.tip(w, Text("Add sisters in ~/.overcode/config.yaml", style=ds.MUTED)))
            return text

        name_w = max(len(s["name"]) for s in self._sisters) + 2
        for i, sister in enumerate(self._sisters):
            sel = i == self.selected_index
            is_enabled = sister["name"] not in self._disabled
            line = ds.item(sel)
            line.append_text(ds.check(is_enabled))
            line.append(" ")
            name_style = "bold" if sel else (ds.TEXT if is_enabled else ds.STATE_OTHER)
            line.append(f"{sister['name']:<{name_w}}", style=name_style)

            # Health indicators
            if not sister.get("reachable", False):
                line.append("unreachable", style=f"bold {ds.ERROR}")
                error = sister.get("last_error", "")
                if error:
                    line.append(f"  {error}", style=ds.MUTED)
            else:
                line.append("web ", style=ds.MUTED)
                line.append("ok", style=ds.STATE_ON)
                line.append("  daemon ", style=ds.MUTED)
                if sister.get("daemon_running", False):
                    line.append("ok", style=ds.STATE_ON)
                else:
                    line.append("down", style=f"bold {ds.ERROR}")
                green = sister.get("green_agents", 0)
                total = sister.get("total_agents", 0)
                line.append(f"  {green}/{total} agents working", style=ds.MUTED)
                version = sister.get("version", "")
                if version:
                    line.append(f"  v{version}", style=ds.MUTED)
            text.append_text(ds.finish(line, w))
            text.append("\n")

        shown = len(self._sisters) - len(self._disabled & {s["name"] for s in self._sisters})
        text.append_text(ds.tip(w, Text(
            f"Ticked sisters' agents are listed here ({shown} of {len(self._sisters)})",
            style=ds.MUTED)))
        return text

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if not self._sisters:
            if key in ("escape", "q", "Q"):
                self._cancel()
                event.stop()
            return

        if self._navigate(event, len(self._sisters)):
            return

        if key in ("space", "enter"):
            self._toggle_current()
            self.refresh()
            event.stop()
        elif key in ("r", "R"):
            self._restart_daemon()
            event.stop()
        elif key in ("a", "A"):
            self._apply()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _toggle_current(self) -> None:
        if not self._sisters:
            return
        name = self._sisters[self.selected_index]["name"]
        if name in self._disabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def _restart_daemon(self) -> None:
        if not self._sisters:
            return
        sister = self._sisters[self.selected_index]
        if not sister.get("reachable", False):
            return  # Can't restart if web server is unreachable
        self.post_message(self.RestartDaemon(
            sister_name=sister["name"],
            sister_url=sister["url"],
            api_key=sister.get("api_key", ""),
        ))

    def _apply(self) -> None:
        self.post_message(self.SelectionChanged(set(self._disabled)))
        self._hide()

    def _cancel(self) -> None:
        self._disabled = set(self._original_disabled)
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, sisters: List[Dict[str, Any]], disabled: Set[str],
             app_ref: Optional[Any] = None) -> None:
        """Display the modal with configured sisters."""
        self._sisters = list(sisters)
        self._disabled = set(disabled)
        self._original_disabled = set(disabled)
        self._save_focus(app_ref)
        self._show()
