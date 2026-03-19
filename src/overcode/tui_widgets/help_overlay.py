"""
Help overlay widget for TUI.

Displays keyboard shortcuts and status reference in a two-column layout.
"""

from textual.widgets import Static
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich import box


class HelpOverlay(Static):
    """Help overlay explaining all TUI metrics and controls"""

    def _build_keybindings(self) -> Text:
        """Build the keybindings column."""
        t = Text()

        def section(title):
            t.append(f"  {title}\n", style="bold bright_white")
            t.append("  " + "─" * 54 + "\n", style="dim")

        def row(k, desc, k2=None, desc2=None):
            t.append(f"  {k:<8}", style="bold cyan")
            if k2:
                t.append(f"{desc:<24}", style="white")
                t.append(f"{k2:<8}", style="bold cyan")
                t.append(f"{desc2}\n", style="white")
            else:
                t.append(f"{desc}\n", style="white")

        section("NAVIGATION")
        row("j/↓", "Next agent", "k/↑", "Previous agent")
        row("b", "Jump to attention", "c", "Sync main + clear")
        row("h/?", "Toggle help", "q", "Quit / detach")
        t.append("\n")

        section("VIEW")
        row("m", "Toggle preview pane", "f", "Fullscreen preview")
        row("r", "Refresh")
        t.append("\n")

        section("DISPLAY")
        row("s", "Summary detail  (low → med → high → full)")
        row("l", "Content  (short → ctx → orders → note → heartbeat)")
        row("S", "Sort  (alpha → status → value → tree)")
        row("$", "Cycle tokens / $ / joules")
        row("C", "Column config", "L", "Column headers")
        row("t", "Timeline", "d", "Daemon panel")
        row("g", "Show killed agents", "Z", "Hide sleeping agents")
        row("D", "Show done agents", "X", "Collapse children")
        row(",/.", "Baseline time ±15m", "0", "Reset baseline")
        row("<", "Timeline scope  (1h → 3h → 6h → 12h → 24h)")
        row("M", "Monochrome mode", "E", "Emoji-free mode")
        t.append("\n")

        section("AGENT CONTROL")
        row("i/:", "Send instruction", "o", "Set standing orders")
        row("a", "Edit annotation", "Enter", "Approve (send Enter)")
        row("1-5", "Send number", "Esc", "Interrupt agent")
        row("n", "New agent", "N", "New remote agent")
        row("x", "Kill agent", "R", "Restart/revive agent")
        row("z", "Toggle sleep", "p", "Pause/resume heartbeat")
        row("V", "Edit agent value", "B", "Edit cost budget")
        row("H", "Heartbeat config", "K", "Hook status detection")
        row("F", "Fork agent", "^T", "Toggle time context")
        row("T", "Handover all (2x)", "I", "Instruction history")
        row("G", "New agent defaults", "U", "Sister visibility")
        t.append("\n")

        section("DAEMON & SERVICES")
        row("[", "Start supervisor", "]", "Stop supervisor")
        row("\\", "Restart monitor", "A", "AI summarizer")
        row("w", "Web dashboard")
        t.append("\n")

        section("OTHER")
        row("y", "Copy mode (mouse sel)", "P", "Sync to tmux pane")
        t.append("\n")

        section("COMMAND BAR  (i or :)")
        row("Enter", "Send instruction", "Esc", "Clear & unfocus")
        row("^E", "Multi-line mode", "^O", "Set standing order")
        row("^S", "Send (multi-line)")
        t.append("\n")

        section("TMUX SPLIT  (overcode tmux)")
        from ..config import get_tmux_toggle_key
        from ..cli.split import TOGGLE_KEY_CHOICES, DEFAULT_TOGGLE_KEY
        _toggle_key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
        _toggle_label = next(
            (label for label, k in TOGGLE_KEY_CHOICES if k == _toggle_key),
            _toggle_key,
        )
        row(_toggle_label, "Toggle pane focus", "=/-", "Resize split")
        row("M-j", "Next agent (from term)", "M-k", "Prev agent (from term)")
        row("M-b", "Go to bell (from term)", "PgUp/Dn", "Scrollback")

        return t

    def _build_status_reference(self) -> Text:
        """Build the status reference column with colored indicators."""
        t = Text()

        def section(title):
            t.append(f"{title}\n", style="bold bright_white")
            t.append("─" * 38 + "\n", style="dim")

        def status(emoji, tl_char, tl_color, name, desc):
            t.append(f"{emoji} ", style="")
            t.append(tl_char, style=tl_color)
            t.append(f"  {name}\n", style="bold white")
            t.append(f"     {desc}\n\n", style="dim")

        section("AGENT STATUSES")

        status("🟢", "█", "green", "Running",
               "Actively working on a task")

        status("💚", "█", "green", "Running (heartbeat)",
               "Auto-resumed by heartbeat")

        status("💛", "▒", "yellow", "Waiting (heartbeat)",
               "Paused — heartbeat will auto-resume")

        status("🟠", "▒", "orange1", "Waiting (approval)",
               "Needs plan or tool use approval")

        status("🔴", "░", "red", "Waiting (user)",
               "Blocked — needs human input")

        status("🟣", "▓", "magenta", "Error",
               "API timeout, rate limit, etc.")

        status("💤", "░", "dim", "Asleep",
               "Paused by human  (z to toggle)")

        status("⚫", "×", "dim", "Terminated",
               "Process exited, shell showing")

        status("✓", "✓", "green", "Done",
               "Child agent completed (D to show)")

        section("SPECIAL INDICATORS")

        t.append("🔔  ", style="")
        t.append("Bell — unvisited stall\n", style="white")
        t.append("     New attention needed, not yet seen\n\n", style="dim")

        t.append("🤿  ", style="")
        t.append("Subagent count\n", style="white")
        t.append("     Active Task tool subprocesses\n\n", style="dim")

        t.append("🐚  ", style="")
        t.append("Background bash count\n", style="white")
        t.append("     Running background shell tasks\n\n", style="dim")

        t.append("👶  ", style="")
        t.append("Child agent count\n", style="white")
        t.append("     Spawned child agents in hierarchy\n\n", style="dim")

        t.append("📋  ", style="")
        t.append("Standing orders active\n", style="white")
        t.append("✓   ", style="")
        t.append("Standing orders complete\n\n", style="white")

        t.append("🤝  ", style="")
        t.append("Agent teams enabled\n\n", style="white")

        section("TIMELINE LEGEND")
        t.append("█", style="green")
        t.append(" active  ", style="dim")
        t.append("💚", style="")
        t.append(" heartbeat start\n", style="dim")
        t.append("▒", style="yellow")
        t.append(" waiting  ", style="dim")
        t.append("░", style="red")
        t.append(" blocked  ", style="dim")
        t.append("▓", style="magenta")
        t.append(" error\n", style="dim")
        t.append("×", style="dim")
        t.append(" exited  ", style="dim")
        t.append("░", style="dim")
        t.append(" asleep   ", style="dim")
        t.append("─", style="dim")
        t.append(" no data\n", style="dim")

        return t

    def render(self):
        layout = Table(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 2),
            expand=True,
        )
        layout.add_column("keys", ratio=3, no_wrap=True)
        layout.add_column("statuses", ratio=2)

        layout.add_row(
            self._build_keybindings(),
            self._build_status_reference(),
        )

        title = Text()
        title.append(" OVERCODE MONITOR HELP ", style="bold bright_white")

        return Panel(
            layout,
            title=title,
            subtitle=Text("Press h or ? to close", style="dim"),
            border_style="bright_blue",
            box=box.DOUBLE,
        )
