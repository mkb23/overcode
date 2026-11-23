# overcode 0.6.4

- **A TAG column (#356).** Each agent's tags as `#name`, `+N` for those that
  do not fit, all of them on hover; it sorts by first tag. It shows while
  your agents' tags differ, and on a row too narrow for every column it is
  the first to give way, so it never pushes a metric off the end.
- **A WFT column** shows the weft task an agent has claimed (`🧶 t-3c9e01
  Title… · stuck +1`), and launches set `WEFT_ACTOR` to the agent's name so
  agents on one machine no longer share claims.
- **Agents a job launches are its agent's children.** A dispatcher run with
  `overcode bash` used to launch top-level agents, outside its agent's
  budget, oversight and kill cascade; `--top-level-launches` keeps the old
  way. `overcode adopt <agent>… --parent <p>`, release's reverse, brings
  such agents under a parent. A job is no longer marked completed in its
  first second while its command runs on.
- **Spin counts delegated work (#555):** each running subagent and batch
  background shell adds one, so Spin can pass the agent count; a yellow
  agent counts its work, not itself as well.
- **Most identity columns switch off.** In the `C` dialog only the status
  light, the alert, ST, DTL, the fold marker and NAME stay fixed; SUBA,
  KIND, TYPE, TAG, WF and WFT now switch per level like any other column
  (KIND was stuck on at full).
- **Fixes.** The TUI no longer crashes now that Claude's usage API has no
  5-hour window (it is gone from the status bar and quota). A done child
  that starts work again goes back to running (#556). Hooks from Claude's
  pre-warmed background worker, and a conversation parked mid-turn, still
  find their agent (#559). Full detail drops its extra columns to fit,
  rather than cutting CTX and the model off the right end.
