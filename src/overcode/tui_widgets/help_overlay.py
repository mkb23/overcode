"""
Help overlay widget for TUI.

Displays keyboard shortcuts and status color explanations.
"""

from textual.widgets import Static
from rich.text import Text


class HelpOverlay(Static):
    """Help overlay explaining all TUI metrics and controls"""

    HELP_TEXT = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                           OVERCODE MONITOR HELP                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  STATUS COLORS                                                               ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  🟢 Running      🟡 No orders      🟠 Wait supervisor      🔴 Wait user      ║
║  💤 Asleep       ⚫ Terminated                                               ║
║                                                                              ║
║  NAVIGATION & VIEW                                                           ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  j/↓     Next agent              k/↑     Previous agent                      ║
║  space   Toggle expand           m       Toggle tree/list mode               ║
║  e       Expand/Collapse all     c       Sync to main + clear                ║
║  h/?     Toggle help             r       Refresh                             ║
║  q       Quit                                                                ║
║                                                                              ║
║  DISPLAY MODES                                                               ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  s       Cycle summary detail    (low → med → full)                          ║
║  l       Cycle summary content   (💬 short → 📖 context → 🎯 orders → ✏️ note)║
║  v       Cycle detail lines      (5 → 10 → 20 → 50)                          ║
║  S       Cycle sort mode         (alpha → status → value)                    ║
║  t       Toggle timeline         d       Toggle daemon panel                 ║
║  g       Show killed agents      Z       Hide sleeping agents                ║
║  ,/.     Baseline time -/+15m    0       Reset baseline to now               ║
║                                                                              ║
║  AGENT CONTROL                                                               ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  i/:     Send instruction        o       Set standing orders                 ║
║  I       Edit annotation         Enter   Approve (send Enter)                ║
║  1-5     Send number             n       New agent                           ║
║  x       Kill agent              R       Restart agent                       ║
║  z       Toggle sleep            V       Edit agent value                    ║
║  b       Jump to red/attention                                               ║
║                                                                              ║
║  DAEMON CONTROL                                                              ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  [       Start supervisor        ]       Stop supervisor                     ║
║  \\       Restart monitor         w       Toggle web dashboard                ║
║                                                                              ║
║  OTHER                                                                       ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  y       Copy mode (mouse sel)   p       Sync to tmux pane                   ║
║  M       Monochrome mode         (for terminals with ANSI issues)            ║
║                                                                              ║
║  COMMAND BAR (i or :)                                                        ║
║  ──────────────────────────────────────────────────────────────────────────  ║
║  Enter       Send instruction    Esc         Clear & unfocus                 ║
║  Ctrl+E      Multi-line mode     Ctrl+O      Set as standing order           ║
║  Ctrl+Enter  Send (multi-line)                                               ║
║                                                                              ║
║                              Press h or ? to close                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

    def render(self) -> Text:
        return Text(self.HELP_TEXT.strip())
