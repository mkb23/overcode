# overcode 0.6.2

- **A held key no longer kills agents one after another.** Holding `x`
  confirmed the kill on the first key repeat, then moved on to the next
  agent; a held key now confirms nothing (#548).
- **Restart and rename never type into a running agent.** Overcode waits for
  the agent's shell to have the terminal back (read from the process table on
  macOS and Linux, not the screen) and refuses if it never exits; this fixes
  stray sessions from agents in Claude's background worker (#549). Agents also
  no longer inherit a host Claude session's identity when tmux was started from
  inside Claude (#550).
- **Model and effort follow `/model` and `/effort` within seconds.** After a
  model switch, effort reads `-` until the next reply says what it is (#539).
- **Auto names follow the work.** With auto-naming on, a name is re-checked
  every 30 min of work and changes when the work has moved on. "Auto-naming
  settings…" in the palette sets the timings, a cap, and the daily cost cap
  (now an amount you type, $10 by default) (#547).
- **Budget reclaim can be manual.** `children.budget_reclaim: manual` (or
  "Budget reclaim" in the palette) keeps a finished child's unused budget with
  the child, so you can keep talking to it, until its parent runs
  `overcode budget reclaim <child>`. The default stays `auto` (#546).
- **Smaller fixes.** A subagent row's empty bell cell no longer makes its time
  read as negative (#545).

**Upgrading:** nothing to convert. If you use auto-naming, names may now
change during long sessions, and the namer's daily cap rises from $0.50 to $10
unless you set `naming.cost_cap_per_day`.
