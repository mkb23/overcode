"""
Embedded terminal pane widget for TUI.

Hosts a live tmux session inside a Textual widget using a PTY + pyte
terminal emulator. Supports modal input: navigation mode (keys go to
Textual) vs passthrough mode (keys go to the tmux pane).
"""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from typing import Optional, TYPE_CHECKING

import pyte
from rich.text import Text
from textual import events
from textual.reactive import reactive
from textual.widget import Widget
from textual.message import Message

if TYPE_CHECKING:
    from .session_summary import SessionSummary


# ANSI color index → Rich color name
_PYTE_COLORS = {
    "black": "black",
    "red": "red",
    "green": "green",
    "brown": "yellow",
    "blue": "blue",
    "magenta": "magenta",
    "cyan": "cyan",
    "white": "white",
    "default": "default",
}

# Bright variants
_PYTE_BRIGHT = {
    "black": "bright_black",
    "red": "bright_red",
    "green": "bright_green",
    "brown": "bright_yellow",
    "blue": "bright_blue",
    "magenta": "bright_magenta",
    "cyan": "bright_cyan",
    "white": "bright_white",
}


def _pyte_color_to_rich(color: str, bold: bool = False) -> str:
    """Convert a pyte color name to a Rich color string."""
    if not color or color == "default":
        return "default"
    # 256-color / 24-bit: pyte gives hex strings like "ff5500"
    if len(color) == 6:
        try:
            int(color, 16)
            return f"#{color}"
        except ValueError:
            pass
    if bold and color in _PYTE_BRIGHT:
        return _PYTE_BRIGHT[color]
    return _PYTE_COLORS.get(color, "default")


class TerminalPane(Widget):
    """Embedded terminal widget showing a live tmux pane.

    Renders a pyte virtual screen as Rich Text. Supports two modes:
    - Navigation mode (default): keys handled by Textual for TUI navigation
    - Passthrough mode: keys forwarded to the embedded tmux session
    """

    can_focus = True

    passthrough = reactive(False)
    """When True, keystrokes are forwarded to the PTY instead of Textual."""

    class PassthroughChanged(Message):
        """Posted when passthrough mode changes."""
        def __init__(self, active: bool) -> None:
            super().__init__()
            self.active = active

    def __init__(self, tmux_session: str = "agents", **kwargs):
        super().__init__(**kwargs)
        self.tmux_session = tmux_session
        self._screen: Optional[pyte.Screen] = None
        self._stream: Optional[pyte.Stream] = None
        self._master_fd: Optional[int] = None
        self._child_pid: Optional[int] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._current_window: Optional[str] = None
        self._session_name: str = ""
        self._cols: int = 80
        self._rows: int = 24
        self._refresh_timer = None
        self._dirty = False

    def compose(self):
        """No child widgets — we render directly."""
        return []

    def on_mount(self) -> None:
        """Start refresh timer."""
        self._refresh_timer = self.set_interval(1 / 15, self._tick)  # 15 FPS

    def on_unmount(self) -> None:
        """Clean up PTY and reader task."""
        self._kill_pty()
        if self._refresh_timer:
            self._refresh_timer.stop()

    def on_resize(self, event: events.Resize) -> None:
        """Update PTY and pyte screen dimensions on resize."""
        new_cols = event.size.width
        new_rows = event.size.height - 1  # Reserve 1 line for mode indicator
        if new_cols < 1 or new_rows < 1:
            return
        if new_cols == self._cols and new_rows == self._rows:
            return
        self._cols = new_cols
        self._rows = new_rows
        if self._screen:
            self._screen.resize(self._rows, self._cols)
        if self._master_fd is not None:
            self._set_pty_size(self._rows, self._cols)
        self._dirty = True

    def attach_window(self, window: str, session_name: str = "") -> None:
        """Attach to a tmux window. Detaches from any current window first."""
        if window == self._current_window:
            return
        self._kill_pty()
        self._current_window = window
        self._session_name = session_name
        self._spawn_pty(window)

    def detach(self) -> None:
        """Detach from the current tmux window."""
        self._kill_pty()
        self._current_window = None
        self._session_name = ""
        self._dirty = True

    def update_from_widget(self, widget: "SessionSummary") -> None:
        """API-compatible with PreviewPane — attach to the widget's tmux window."""
        window = widget.session.tmux_window
        name = widget.session.name
        if window and window != self._current_window:
            self.attach_window(window, session_name=name)
        elif not window:
            self.detach()
        else:
            self._session_name = name

    def _spawn_pty(self, window: str) -> None:
        """Fork a PTY running tmux attach to the given window."""
        if window.isdigit():
            target = f"{self.tmux_session}:{window}"
        else:
            target = f"{self.tmux_session}:={window}"

        # Create pyte screen + stream
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.Stream(self._screen)

        # Fork PTY
        pid, fd = pty.fork()
        if pid == 0:
            # Child process — exec tmux attach
            os.execlp("tmux", "tmux", "attach-session", "-t", target)
            os._exit(1)

        self._master_fd = fd
        self._child_pid = pid

        # Set PTY size
        self._set_pty_size(self._rows, self._cols)

        # Make master_fd non-blocking
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Start async reader
        loop = asyncio.get_event_loop()
        self._reader_task = loop.create_task(self._read_pty())

    async def _read_pty(self) -> None:
        """Async reader that feeds PTY output into the pyte stream."""
        loop = asyncio.get_event_loop()
        fd = self._master_fd
        if fd is None:
            return

        while True:
            try:
                future = loop.create_future()

                def _readable():
                    if not future.done():
                        future.set_result(True)

                loop.add_reader(fd, _readable)
                try:
                    await future
                finally:
                    loop.remove_reader(fd)

                data = os.read(fd, 65536)
                if not data:
                    break
                self._stream.feed(data.decode("utf-8", errors="replace"))
                self._dirty = True

            except OSError:
                break
            except asyncio.CancelledError:
                break

    def _tick(self) -> None:
        """Called by timer — refresh widget if screen is dirty."""
        if self._dirty:
            self._dirty = False
            self.refresh()

    def _kill_pty(self) -> None:
        """Kill the PTY child process and clean up."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        self._reader_task = None

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, signal.SIGTERM)
                os.waitpid(self._child_pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            self._child_pid = None

        self._screen = None
        self._stream = None

    def _set_pty_size(self, rows: int, cols: int) -> None:
        """Set the PTY window size via ioctl."""
        if self._master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
                if self._child_pid:
                    os.kill(self._child_pid, signal.SIGWINCH)
            except OSError:
                pass

    def on_key(self, event: events.Key) -> None:
        """Handle key events — forward to PTY in passthrough mode."""
        # Ctrl+] always exits passthrough mode
        if event.key == "ctrl+right_square_bracket":
            if self.passthrough:
                self.passthrough = False
                event.stop()
                return

        # Enter toggles into passthrough mode (only when focused)
        if not self.passthrough and event.key == "enter":
            self.passthrough = True
            event.stop()
            return

        if self.passthrough:
            self._send_key(event)
            event.stop()

    def watch_passthrough(self, active: bool) -> None:
        """React to passthrough mode changes."""
        self._dirty = True
        self.post_message(self.PassthroughChanged(active))

    def _send_key(self, event: events.Key) -> None:
        """Forward a Textual key event to the PTY as bytes."""
        if self._master_fd is None:
            return

        char = self._key_to_bytes(event)
        if char:
            try:
                os.write(self._master_fd, char)
            except OSError:
                pass

    @staticmethod
    def _key_to_bytes(event: events.Key) -> Optional[bytes]:
        """Convert a Textual Key event to bytes for the terminal."""
        key = event.key

        # Direct character input
        if event.character and len(event.character) == 1:
            if key.startswith("ctrl+") and len(key) == 6:
                letter = key[-1]
                if 'a' <= letter <= 'z':
                    return bytes([ord(letter) - ord('a') + 1])
            return event.character.encode("utf-8")

        # Special keys
        key_map = {
            "enter": b"\r",
            "tab": b"\t",
            "escape": b"\x1b",
            "backspace": b"\x7f",
            "delete": b"\x1b[3~",
            "up": b"\x1b[A",
            "down": b"\x1b[B",
            "right": b"\x1b[C",
            "left": b"\x1b[D",
            "home": b"\x1b[H",
            "end": b"\x1b[F",
            "pageup": b"\x1b[5~",
            "pagedown": b"\x1b[6~",
            "insert": b"\x1b[2~",
            "f1": b"\x1bOP",
            "f2": b"\x1bOQ",
            "f3": b"\x1bOR",
            "f4": b"\x1bOS",
            "f5": b"\x1b[15~",
            "f6": b"\x1b[17~",
            "f7": b"\x1b[18~",
            "f8": b"\x1b[19~",
            "f9": b"\x1b[20~",
            "f10": b"\x1b[21~",
            "f11": b"\x1b[23~",
            "f12": b"\x1b[24~",
            "space": b" ",
        }

        if key in key_map:
            return key_map[key]

        # Ctrl combinations
        if key.startswith("ctrl+"):
            suffix = key[5:]
            if len(suffix) == 1 and 'a' <= suffix <= 'z':
                return bytes([ord(suffix) - ord('a') + 1])

        return None

    def render(self) -> Text:
        """Render the pyte screen as Rich Text."""
        output = Text()
        width = self._cols

        # Mode indicator line
        if self.passthrough:
            indicator = " INTERACTIVE "
            pad = "─" * max(0, width - len(indicator) - len(self._session_name) - 5)
            output.append(f"─── {self._session_name} ", style="bold cyan")
            output.append(pad, style="dim")
            output.append(indicator, style="bold black on green")
            output.append("\n")
        else:
            indicator = " PREVIEW (Enter=interactive, Ctrl+]=back) "
            pad = "─" * max(0, width - len(indicator) - len(self._session_name) - 5)
            output.append(f"─── {self._session_name} ", style="bold cyan")
            output.append(pad, style="dim")
            output.append(indicator, style="dim")
            output.append("\n")

        if self._screen is None:
            output.append("(not attached)", style="dim italic")
            return output

        # Render pyte screen buffer
        screen = self._screen
        for y in range(screen.lines):
            line = screen.buffer[y]
            for x in range(screen.columns):
                char = line[x]
                ch = char.data or " "

                style_parts = []
                fg = _pyte_color_to_rich(char.fg, bold=char.bold)
                bg = _pyte_color_to_rich(char.bg)
                if fg != "default":
                    style_parts.append(fg)
                if bg != "default":
                    style_parts.append(f"on {bg}")
                if char.bold:
                    style_parts.append("bold")
                if char.italics:
                    style_parts.append("italic")
                if char.underscore:
                    style_parts.append("underline")
                if char.strikethrough:
                    style_parts.append("strike")
                if char.reverse:
                    style_parts.append("reverse")

                # Show cursor position in passthrough mode
                if (self.passthrough and y == screen.cursor.y
                        and x == screen.cursor.x):
                    style_parts.append("reverse")

                style = " ".join(style_parts) if style_parts else ""
                output.append(ch, style=style)

            if y < screen.lines - 1:
                output.append("\n")

        return output
