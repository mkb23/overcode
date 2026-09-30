"""
The TUI side of view control (#484): apply `overcode view` commands, publish view state.

Once a second the TUI reads new lines from its control inbox, applies each
through the same code its own keys and dialogs use, and appends an ack with
the result. It then rewrites tui_view_state.json if anything in it changed
(and every few seconds anyway, as a liveness signal).
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Any, Optional

from ..keymap import keymap_of
from ..view_control import (
    VERBS,
    ControlInbox,
    ack_path,
    append_jsonl,
    suggest,
    view_state_path,
    write_json_atomic,
)

STATE_KEEPALIVE_SECONDS = 4.0
RECENT_ACTIONS = 20


class ViewCommandError(Exception):
    """A command that can't be applied; the message says what would work."""


class ViewControlMixin:
    """Mixed into SupervisorTUI."""

    _control_inbox: ControlInbox
    _view_state_last: Optional[dict] = None
    _view_state_written_at: float = 0.0
    _recent_actions: deque

    def _init_view_control(self) -> None:
        self._control_inbox = ControlInbox(self.tmux_session)
        self._recent_actions = deque(maxlen=RECENT_ACTIONS)

    def note_recent_action(self, action: str, via: str) -> None:
        self._recent_actions.append({"action": action, "via": via, "t": round(time.time(), 1)})

    # ── the tick ───────────────────────────────────────────────────────

    def _view_control_tick(self) -> None:
        for cmd in self._control_inbox.poll():
            self._run_view_command(cmd)
        self._publish_view_state()
        try:
            self._mentor_tick()
        except Exception:
            pass

    def _run_view_command(self, cmd: dict) -> None:
        verb, args, via = cmd.get("verb", ""), cmd.get("args") or {}, cmd.get("via", "agent")
        ack: dict[str, Any] = {"id": cmd["id"], "t": time.time(), "verb": verb}
        try:
            # Only the published verbs: _view_state, _view_signature and the
            # tick are methods too, and are not commands (#502).
            handler = getattr(self, f"_view_{verb}", None) if verb in VERBS else None
            if handler is None:
                raise ViewCommandError(f"unknown verb '{verb}'")
            ack["result"] = handler(**args)
            ack["ok"] = True
        except ViewCommandError as e:
            ack["ok"], ack["error"] = False, str(e)
        except TypeError as e:
            ack["ok"], ack["error"] = False, f"bad arguments for '{verb}': {e}"
        except Exception as e:  # never let a command take the TUI down
            ack["ok"], ack["error"] = False, f"{type(e).__name__}: {e}"
        self.record_activity("action", action=f"view:{verb}", via=via, ok=ack["ok"])
        self.note_recent_action(f"view:{verb}", via)
        try:
            append_jsonl(ack_path(self.tmux_session), ack)
        except OSError:
            pass
        self._publish_view_state(force=True)

    # ── verbs ──────────────────────────────────────────────────────────

    def _resolve_column(self, name: str):
        from ..summary_columns import SUMMARY_COLUMNS
        key = name.strip().lower()
        for col in SUMMARY_COLUMNS:
            if key in (col.id.lower(), (col.name or "").lower(), (col.header or "").strip().lower()):
                return col
        names = [c.id for c in SUMMARY_COLUMNS] + [c.name for c in SUMMARY_COLUMNS if c.name]
        hint = suggest(name, names)
        raise ViewCommandError(f"unknown column '{name}'"
                               + (f" — did you mean {', '.join(hint)}?" if hint else
                                  " (overcode view columns list shows them all)"))

    def _level_arg(self, level: Optional[str]) -> str:
        if level is None:
            return self.SUMMARY_LEVELS[self.summary_level_index]
        if level not in self.SUMMARY_LEVELS:
            raise ViewCommandError(f"unknown level '{level}' — one of {', '.join(self.SUMMARY_LEVELS)}")
        return level

    def _view_columns(self, op: str, ids: Optional[list] = None, level: Optional[str] = None) -> dict:
        lvl = self._level_arg(level)
        overrides = dict(self._prefs.column_config.get(lvl, {}))
        cols = [self._resolve_column(i) for i in (ids or [])]
        if op in ("show", "hide"):
            if not cols:
                raise ViewCommandError(f"columns {op} needs at least one column")
            for col in cols:
                overrides[col.id] = op == "show"
        elif op == "reset":
            if cols:
                for col in cols:
                    overrides.pop(col.id, None)
            else:
                overrides = {}
        else:
            raise ViewCommandError(f"unknown columns op '{op}' — show, hide or reset")
        self._apply_column_config(lvl, overrides)
        return {"level": lvl, "changed": [c.id for c in cols], "visible": self._visible_column_ids(lvl)}

    def _visible_column_ids(self, level: str) -> list:
        from ..summary_columns import SUMMARY_COLUMNS, resolve_column_visible
        overrides = self._prefs.column_config.get(level, {})
        return [c.id for c in SUMMARY_COLUMNS
                if not c.cli_only and resolve_column_visible(c, level, overrides, self.uniform_columns)]

    def _view_sort(self, column: str, descending: Optional[bool] = None) -> dict:
        from ..tui_logic import sort_descending, sort_mode_for_column
        if column.lower() in ("tree", "by_tree"):
            mode = "by_tree"
        else:
            col = self._resolve_column(column)
            if col.sort_key is None:
                raise ViewCommandError(f"{col.name or col.id} is not sortable")
            mode = sort_mode_for_column(col.id)
        if mode != self._prefs.sort_mode:
            self.set_sort_mode(mode)
        if descending is not None and mode != "by_tree" \
                and sort_descending(mode, self._prefs.sort_reversed) != descending:
            self.set_sort_mode(mode)  # choosing the current sort again reverses it
        return {"mode": self._prefs.sort_mode,
                "descending": sort_descending(self._prefs.sort_mode, self._prefs.sort_reversed)}

    def _view_detail(self, level: str) -> dict:
        lvl = self._level_arg(level)
        self._set_summary_level(lvl)
        return {"level": lvl}

    def _view_filter(self, tag: Optional[str] = None) -> dict:
        self.tag_filter = tag or None
        self.update_session_widgets()
        return {"tag": self.tag_filter}

    def _view_focus(self, agent: str) -> dict:
        widgets = self._get_widgets_in_session_order()
        for i, w in enumerate(widgets):
            if agent in (w.session.name, w.session.id):
                self._user_navigated = True
                self.focused_session_index = i
                return {"agent": w.session.name}
        names = [w.session.name for w in widgets]
        hint = suggest(agent, names)
        raise ViewCommandError(f"no agent '{agent}' in the list"
                               + (f" — did you mean {', '.join(hint)}?" if hint else ""))

    def _palette_command(self, action: str):
        from ..command_palette import COMMANDS
        for cmd in COMMANDS:
            if cmd.action == action:
                return cmd
        hint = suggest(action, [c.action for c in COMMANDS])
        raise ViewCommandError(f"no palette action '{action}'"
                               + (f" — did you mean {', '.join(hint)}?" if hint else ""))

    def _view_toggle(self, action: str) -> dict:
        from ..view_control import toggle_refusal
        cmd = self._palette_command(action)
        refusal = toggle_refusal(cmd.category)
        if refusal is not None:
            raise ViewCommandError(f"'{action}' {refusal}")
        if self.compact and action in self._COMPACT_BLOCKED_ACTIONS:
            raise ViewCommandError(f"'{action}' is not available in split mode")
        getattr(self, f"action_{action}")()
        result: dict[str, Any] = {"action": action, "title": cmd.title}
        if cmd.state is not None:
            try:
                sv = cmd.state(self)
                if sv.index is not None:
                    result["state"] = sv.options[sv.index]
            except Exception:
                pass
        return result

    def _view_notify(self, text: str, severity: str = "information") -> dict:
        if severity not in ("information", "warning", "error"):
            severity = "information"
        self.notify(text, severity=severity, timeout=8)
        return {"shown": True}

    def _view_point(self, action: str) -> dict:
        """Show the user where something is: its key, or where to find it in the palette."""
        cmd = self._palette_command(action)
        keys = keymap_of(self).keys_for(action)
        if keys:
            from ..command_palette import key_label
            how = " or ".join(key_label(k) for k in keys)
            self.notify(f"Press {how}  —  {cmd.title}", severity="information", timeout=12)
        else:
            self.notify(f"Press / and type “{cmd.title}”", severity="information", timeout=12)
        return {"action": action, "keys": keys}

    # ── view state ─────────────────────────────────────────────────────

    def _view_state(self) -> dict:
        from ..tui_logic import sort_column_for_mode, sort_descending
        level = self.SUMMARY_LEVELS[self.summary_level_index]
        focused = None
        widget = self._get_focused_widget()
        if widget is not None:
            s = widget.session
            focused = {
                "name": s.name, "id": s.id, "status": getattr(widget, "detected_status", None),
                "repo": getattr(s, "repo_name", None), "branch": getattr(s, "branch", None),
                "directory": getattr(s, "start_directory", None),
                "tags": list(getattr(s, "tags", None) or []),
                "backend": getattr(s, "backend", None),
            }
        try:
            dialog = self._activity_current_dialog()
        except Exception:
            dialog = None
        return {
            "tmux_session": self.tmux_session,
            "mode": getattr(self, "tui_mode", "agents"),
            "level": level,
            "focused": focused,
            "agents_shown": len(self._get_widgets_in_session_order()),
            "visible_columns": self._visible_column_ids(level),
            "uniform_columns": {k: v for k, v in (self.uniform_columns or {}).items()},
            "column_overrides": dict(self._prefs.column_config.get(level, {})),
            "sort": {"mode": self._prefs.sort_mode,
                     "column": sort_column_for_mode(self._prefs.sort_mode),
                     "descending": sort_descending(self._prefs.sort_mode, self._prefs.sort_reversed)},
            "tag_filter": self.tag_filter,
            "show_terminated": bool(self.show_terminated),
            "hide_asleep": bool(self.hide_asleep),
            "show_done": bool(self.show_done),
            "compact": bool(self.compact),
            "dialog": dialog,
            "recent_actions": list(self._recent_actions),
        }

    def _view_signature(self) -> tuple:
        """Everything the view state depends on, cheaply: the state is rebuilt only when it moves."""
        widgets = self._get_widgets_in_session_order()
        focused = self._get_focused_widget()
        return (
            self.summary_level_index, self.focused_session_index, len(widgets),
            focused.session.id if focused is not None else None,
            getattr(focused, "detected_status", None) if focused is not None else None,
            self._prefs.sort_mode, self._prefs.sort_reversed, self.tag_filter,
            repr(self._prefs.column_config), repr(self.uniform_columns),
            bool(self.show_terminated), bool(self.hide_asleep), bool(self.show_done),
            getattr(self, "tui_mode", ""), len(self._recent_actions),
            self._recent_actions[-1]["t"] if self._recent_actions else None,
            getattr(self, "_activity_dialog", None),
        )

    def _publish_view_state(self, force: bool = False) -> None:
        """Rewrite tui_view_state.json when the view changed; otherwise only touch it
        every few seconds, so a reader can tell the TUI is alive."""
        now = time.time()
        path = view_state_path(self.tmux_session)
        try:
            sig = self._view_signature()
        except Exception:
            return
        if not force and sig == getattr(self, "_view_sig_last", None):
            if now - self._view_state_written_at >= STATE_KEEPALIVE_SECONDS:
                self._view_state_written_at = now
                try:
                    os.utime(path)
                except OSError:
                    self._view_sig_last = None  # gone: rewrite next tick
            return
        try:
            state = self._view_state()
        except Exception:
            return
        self._view_sig_last = sig
        self._view_state_last = state
        self._view_state_written_at = now
        try:
            write_json_atomic(path, {**state, "pid": os.getpid(), "updated": round(now, 2)})
        except OSError:
            pass
