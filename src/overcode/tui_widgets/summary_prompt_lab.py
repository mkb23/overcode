"""
Summary prompt lab (#491): edit a summarizer prompt and watch it run.

The top half is the prompt (short or context) in a text editor; the
bottom half is what that prompt produces for up to six live agents,
re-run about every two seconds once the draft has settled — only for
agents whose inputs changed (see prompt_lab.py), so a quiet fleet and a
settled draft cost nothing. Each result sits over the live summarizer's
current text for the same agent, for comparison.

Saving writes ~/.overcode/prompts/summary-<mode>.md, which the live
summarizer picks up on its next call; saving the built-in text (Ctrl+L
then Ctrl+S) deletes the file instead, so the default applies again.

Keys (the editor keeps its own: arrows, Ctrl+Z undo, Ctrl+C/X/V…):
    Ctrl+S  save             Ctrl+T  switch short / context
    Ctrl+R  revert to saved  Ctrl+L  load the built-in default
    Esc     close (twice to discard unsaved edits)
"""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea

from .. import summarizer_prompts as sp
from ..prompt_lab import LabResult, LabSample, PromptLabRunner
from . import dialog_style as ds

RUN_INTERVAL = 2.0   # seconds between lab passes
SETTLE_SECONDS = 1.0  # a draft must be this still before it is run

MODE_LABELS = {"short": "short", "context": "context"}


class SummaryPromptLab(Vertical):
    """The lab dialog. Mounted hidden; ``show()`` opens it."""

    DEFAULT_CLASSES = "modal"
    TITLE = "Summary prompts"
    WIDTH = 120

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+t", "switch_mode", "Short/context", show=False),
        Binding("ctrl+r", "revert", "Revert", show=False),
        Binding("ctrl+l", "load_default", "Default", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    class Closed(Message):
        pass

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mode: str = "short"
        self._saved: str = ""
        self._runner: Optional[PromptLabRunner] = None
        self._samples_fn: Optional[Callable[[str], List[LabSample]]] = None
        self._blocked_fn: Optional[Callable[[], Optional[str]]] = None
        self._samples: List[LabSample] = []
        self._available: bool = True
        self._app_ref: Any = None
        self._previous_focus: Any = None
        self._timer: Any = None
        self._edited_at: float = 0.0
        self._confirm_discard: bool = False
        self._note: Optional[Text] = None
        self._loaded_text: Optional[str] = None  # last text set by code, not typed
        self._inner_width: int = self.WIDTH - 4

    def compose(self):
        yield Static(id="lab-head")
        yield TextArea(id="lab-editor", soft_wrap=True, show_line_numbers=False)
        yield Static(id="lab-results")

    # ── public api ───────────────────────────────────────────────────────

    def show(
        self,
        *,
        runner: PromptLabRunner,
        samples: Callable[[str], List[LabSample]],
        available: bool,
        blocked: Optional[Callable[[], Optional[str]]] = None,
        app_ref: Any = None,
    ) -> None:
        """Open the lab. ``samples(mode)`` is asked for the agents to try
        each pass (with their live summaries for ``mode``); ``blocked()``
        returns why no calls may be made right now (the cost cap), or None."""
        self._runner = runner
        self._samples_fn = samples
        self._blocked_fn = blocked
        self._available = available
        self._app_ref = app_ref
        self._previous_focus = getattr(app_ref, "focused", None) if app_ref else None
        self._samples = samples(self.mode)
        self._load_mode(self.mode)
        self.relayout()
        self.add_class("visible")
        self.editor.focus()
        if self._timer is None:
            self._timer = self.set_interval(RUN_INTERVAL, self._tick)
        else:
            self._timer.resume()
        self._tick()

    def hide(self) -> None:
        self.remove_class("visible")
        if self._timer is not None:
            self._timer.pause()
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None
        self.post_message(self.Closed())

    @property
    def editor(self) -> TextArea:
        return self.query_one("#lab-editor", TextArea)

    @property
    def draft(self) -> str:
        return self.editor.text

    @property
    def dirty(self) -> bool:
        return self.draft != self._saved

    # ── layout ───────────────────────────────────────────────────────────

    def relayout(self) -> None:
        """Size and centre for the terminal, like ModalBase.relayout."""
        try:
            screen_w, screen_h = self.app.size
        except Exception:
            return
        width = max(40, min(self.WIDTH, screen_w - 2))
        top = 1 if screen_h < 30 else 2
        self._inner_width = width - 4
        self.styles.width = width
        self.styles.max_height = max(12, screen_h - top - 1)
        self.styles.offset = (max(0, (screen_w - width) // 2), top)
        # Results take 2 lines an agent plus a header and the tip; the
        # editor gets what is left, within reason.
        results_h = 2 * len(self._samples or [None] * 3) + 4
        editor_h = screen_h - top - 1 - 2 - 2 - results_h
        self.editor.styles.height = max(5, min(18, editor_h))
        self.border_title = self.TITLE
        self.border_subtitle = ds.hints(
            ("^S", "save"), ("^T", "short/context"), ("^R", "revert"),
            ("^L", "default"), ("esc", "close"),
        )
        self._draw()

    # ── prompt state ─────────────────────────────────────────────────────

    def _load_mode(self, mode: str) -> None:
        self.mode = mode
        self._saved = sp.load_prompt(mode)
        self._set_text(self._saved)
        self._edited_at = 0.0
        self._confirm_discard = False
        if self._runner:
            self._runner.reset()
        self._draw()

    def _set_text(self, text: str) -> None:
        """Replace the editor's text from code. The Changed message this
        posts arrives later; remembering the text lets the handler tell it
        from typing (which clears the status note and restarts settling)."""
        self._loaded_text = text
        self.editor.load_text(text)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self.draft == self._loaded_text:
            self._loaded_text = None
            self._draw()
            return
        self._loaded_text = None
        self._edited_at = time.monotonic()
        self._confirm_discard = False
        self._note = None
        self._draw()

    def action_save(self) -> None:
        text = self.draft
        if text.strip() == sp.DEFAULT_PROMPTS[self.mode].strip() or not text.strip():
            sp.reset_prompt(self.mode)
            self._note = Text("saved — using the built-in default (file removed)", style=ds.STATE_ON)
            text = sp.DEFAULT_PROMPTS[self.mode]
            self._set_text(text)
        else:
            path = sp.save_prompt(self.mode, text)
            self._note = Text(f"saved to {_tilde(path)} — the live summarizer uses it now", style=ds.STATE_ON)
        self._saved = text
        self._confirm_discard = False
        self._draw()

    def action_revert(self) -> None:
        self._set_text(self._saved)
        self._note = Text("reverted to the saved prompt", style=ds.MUTED)
        self._draw()

    def action_load_default(self) -> None:
        self._set_text(sp.DEFAULT_PROMPTS[self.mode])
        self._note = Text("built-in default loaded — ^S to save it", style=ds.MUTED)
        self._draw()

    def action_switch_mode(self) -> None:
        if self.dirty and not self._confirm_discard:
            self._confirm_discard = True
            self._note = Text("unsaved edits — ^S to save, or ^T again to discard", style=ds.WARN)
            self._draw()
            return
        self._load_mode("context" if self.mode == "short" else "short")
        self._tick()

    def action_close(self) -> None:
        if self.dirty and not self._confirm_discard:
            self._confirm_discard = True
            self._note = Text("unsaved edits — ^S to save, or esc again to discard", style=ds.WARN)
            self._draw()
            return
        self.hide()

    # ── running ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Start a lab pass in a thread, unless one is running or the
        draft is still being typed."""
        if not self.has_class("visible") or self._runner is None or not self._available:
            self._draw()
            return
        if self._blocked_fn is not None and self._blocked_fn():
            self._draw()
            return
        if self._runner.busy:
            return
        if self._edited_at and time.monotonic() - self._edited_at < SETTLE_SECONDS:
            return
        if self._samples_fn is not None:
            self._samples = self._samples_fn(self.mode)
        mode, template, samples = self.mode, self.draft, list(self._samples)
        self._draw()
        self.run_worker(
            lambda: self._runner.run(mode, template, samples, on_update=self._redraw_from_thread),
            thread=True, group="prompt-lab", exit_on_error=False,
        )

    def _redraw_from_thread(self) -> None:
        try:
            self.app.call_from_thread(self._draw)
        except Exception:
            pass

    # ── drawing ──────────────────────────────────────────────────────────

    def _draw(self) -> None:
        try:
            head = self.query_one("#lab-head", Static)
            results = self.query_one("#lab-results", Static)
        except Exception:
            return
        head.update(self._render_head())
        results.update(self._render_results())

    def _render_head(self) -> Text:
        w = self._inner_width
        t = Text(no_wrap=True, overflow="crop")
        line = Text(" ")
        line.append("Prompt  ", style=f"bold {ds.TEXT}")
        line.append_text(ds.options(tuple(MODE_LABELS), self.mode))
        line.append("   ")
        path = sp.prompt_path(self.mode)
        if self.dirty:
            state = Text("● unsaved", style=f"bold {ds.WARN}")
        elif sp.is_customised(self.mode):
            state = Text(f"saved · {_tilde(path)}", style=ds.MUTED)
        else:
            state = Text(f"built-in default · ^S saves to {_tilde(path)}", style=ds.MUTED)
        # The state (with its path) gives way before the mode choice does
        state = ds.fit(state, max(8, w - cell_len(line.plain) - 1)) if cell_len(state.plain) > w - cell_len(line.plain) - 1 else state
        t.append_text(ds.spread(line, state, w))
        t.append("\n")
        t.append_text(ds.finish(self._warning_line(), w))
        return t

    def _warning_line(self) -> Text:
        draft = self.draft
        if self._note is not None:
            return Text(" ").append_text(self._note)
        if sp.missing_pane_content(draft):
            return Text(" no {pane_content} — the prompt never sees the terminal", style=ds.WARN)
        unknown = sp.unknown_placeholders(draft)
        if unknown:
            names = ", ".join("{" + n + "}" for n in unknown)
            return Text(f" left as typed (not placeholders): {names}", style=ds.WARN)
        return Text(
            " placeholders: {pane_content} {lines} {previous_summary} {status}",
            style=ds.STATE_OTHER,
        )

    def _render_results(self) -> Text:
        w = self._inner_width
        t = Text(no_wrap=True, overflow="crop")
        runner = self._runner
        left = Text(" RESULTS ", style=f"bold {ds.MUTED}")
        if runner is not None:
            right = Text(
                f"every {RUN_INTERVAL:g}s when inputs change · {runner.calls} calls · ${runner.spent_usd:.4f} ",
                style=ds.STATE_OTHER,
            )
        else:
            right = Text("")
        rule_len = max(0, w - cell_len(left.plain) - cell_len(right.plain) - 1)
        left.append("─" * rule_len, style=ds.STATE_OTHER)
        t.append_text(ds.spread(left, right, w))
        if not self._available:
            t.append("\n")
            t.append_text(ds.finish(Text(
                " no summarizer API key — set summarizer.api_key_var in ~/.overcode/config.yaml "
                "(editing and saving still work)", style=ds.WARN), w))
            return t
        blocked = self._blocked_fn() if self._blocked_fn is not None else None
        if blocked:
            t.append("\n")
            t.append_text(ds.finish(Text(f" {blocked}", style=ds.ERROR), w))
            return t
        if not self._samples:
            t.append("\n")
            t.append_text(ds.finish(Text(" no running local agents to try the prompt on", style=ds.MUTED), w))
            return t
        name_w = min(18, max(cell_len(s.name) for s in self._samples) + 1)
        for sample in self._samples:
            result = runner.results.get(sample.session_id) if runner else None
            t.append("\n")
            t.append_text(self._result_line(sample, result, name_w, w))
            t.append("\n")
            live = Text(" " * (name_w + 2))
            live.append("now: ", style=ds.STATE_OTHER)
            live.append(sample.live or "—", style=ds.STATE_OTHER)
            t.append_text(ds.finish(live, w))
        return t

    def _result_line(self, sample: LabSample, result: Optional[LabResult], name_w: int, w: int) -> Text:
        line = Text(" ")
        line.append(f"{sample.name[:name_w]:<{name_w}} ", style=f"bold {ds.TEXT}")
        if result is None or (result.text is None and not result.error):
            body = Text("…", style=ds.MUTED)
            meta = Text("")
        elif result.error:
            body = Text("(no response — API error or timeout)", style=ds.ERROR)
            meta = Text("")
        else:
            unchanged = result.text.strip().upper() == "UNCHANGED"
            body = Text(result.text, style=ds.MUTED if unchanged else "bold")
            meta = Text(
                f"{result.seconds:.1f}s · {result.input_tokens}+{result.output_tokens} tok",
                style=ds.STATE_OTHER,
            )
        if result is not None and result.running:
            meta = Text("running…", style=ds.ACCENT)
        line.append_text(body)
        return ds.spread(line, meta, w)


def _tilde(path) -> str:
    import os
    home = os.path.expanduser("~")
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s
