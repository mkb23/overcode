"""
Jump-to-agent modal — VSCode-style fuzzy filter + select (#420).

Press Ctrl+P to open. Type to filter the agent list, up/down to navigate,
Enter to jump, Esc to cancel.
"""

from dataclasses import dataclass
from typing import List, Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase


@dataclass
class JumpCandidate:
    """One row the jump modal can show."""
    session_id: str
    name: str
    repo: str = ""
    branch: str = ""


class JumpModal(ModalBase):
    """Modal that filters agents by substring and jumps to the chosen one."""

    class AgentSelected(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class Cancelled(Message):
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: List[JumpCandidate] = []
        self._filtered: List[JumpCandidate] = []
        self._query: str = ""

    def render(self) -> Text:
        text = Text()
        text.append("Jump to agent\n", style="bold cyan")
        text.append("type:filter  ↑/↓:move  enter:jump  esc:cancel\n\n", style="dim")

        # Query line with a trailing cursor block
        text.append("> ", style="bold cyan")
        text.append(self._query, style="bold")
        text.append("▎\n\n", style="bold cyan")

        if not self._filtered:
            if self._query:
                text.append("  (no matches)\n", style="dim italic")
            else:
                text.append("  (no agents)\n", style="dim italic")
            return text

        for i, cand in enumerate(self._filtered):
            is_selected = i == self.selected_index
            prefix = "> " if is_selected else "  "
            name_style = "bold cyan" if is_selected else "bold"
            meta_style = "cyan" if is_selected else "dim"

            text.append(prefix, style="bold cyan" if is_selected else "")
            text.append(cand.name, style=name_style)
            meta_parts = []
            if cand.repo:
                meta_parts.append(cand.repo)
            if cand.branch and cand.branch != cand.repo:
                meta_parts.append(cand.branch)
            if meta_parts:
                text.append("  " + " · ".join(meta_parts), style=meta_style)
            text.append("\n")

        return text

    def on_key(self, event: events.Key) -> None:
        key = event.key

        if key == "enter":
            self._select()
            event.stop()
            return
        if key in ("escape",):
            self._cancel()
            event.stop()
            return
        if key in ("up",):
            if self._filtered:
                self.selected_index = (self.selected_index - 1) % len(self._filtered)
                self.refresh()
            event.stop()
            return
        if key in ("down",):
            if self._filtered:
                self.selected_index = (self.selected_index + 1) % len(self._filtered)
                self.refresh()
            event.stop()
            return
        if key in ("backspace", "ctrl+h"):
            self._query = self._query[:-1]
            self._recompute()
            event.stop()
            return
        if key == "ctrl+u":
            self._query = ""
            self._recompute()
            event.stop()
            return
        if event.character and event.character.isprintable() and len(event.character) == 1:
            self._query += event.character
            self._recompute()
            event.stop()
            return
        # Swallow everything else so nothing leaks to parent
        event.stop()

    def _recompute(self) -> None:
        self._filtered = filter_candidates(self._candidates, self._query)
        self.selected_index = 0
        self.refresh()

    def _select(self) -> None:
        if not self._filtered:
            return
        chosen = self._filtered[self.selected_index]
        self.post_message(self.AgentSelected(chosen.session_id))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, candidates: List[JumpCandidate], app_ref: Optional[Any] = None) -> None:
        """Display the modal seeded with the full agent list."""
        self._candidates = list(candidates)
        self._query = ""
        self._filtered = list(candidates)
        self._save_focus(app_ref)
        self._show()


def filter_candidates(candidates: List[JumpCandidate], query: str) -> List[JumpCandidate]:
    """Substring-filter candidates by query, case-insensitively.

    Matches against name, repo, and branch. Results are ordered so that
    name matches come first, then repo/branch. Pure function — easy to
    unit-test without a textual app.
    """
    if not query:
        return list(candidates)
    q = query.lower()
    name_hits: List[JumpCandidate] = []
    other_hits: List[JumpCandidate] = []
    for c in candidates:
        if q in c.name.lower():
            name_hits.append(c)
        elif q in c.repo.lower() or q in c.branch.lower():
            other_hits.append(c)
    return name_hits + other_hits
