# overcode 0.6.4

- **Typing into the TUI by mistake no longer scrambles it (#560).** A burst
  of keys typed like prose is caught. overcode ignores the rest of it, undoes
  what the first keys toggled or opened, and says so. Quitting, launching and
  sending to an agent wait until it is clear the keys weren't typing. Ctrl+Z
  (or "Undo last change") undoes setting changes. Rarely used settings and
  daemon keys (`M Y E L [ ] \ A w G W U K`) are palette-only now;
  `keys: {preset: classic}` brings them back, and `tui.typing_guard: false`
  turns the guard off.
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
  rather than cutting CTX and the model off the right end. `restart --model`
  now wins over a `-m` passed through at launch (opencode, codex, grok,
  hermes). codex 0.164's folder-trust dialog no longer eats the launch
  prompt. opencode agents get their PR number from their own `gh pr
  create` output (#532). Emoji that some terminals drew at the wrong width
  are replaced (#461).
- **Cheaper at scale (#528).** Following a running child costs one tmux call
  per poll, not two. A sister's `/api/status` lists panes once and reuses
  unchanged captures. Jobs and the summarizer skip work for panes that
  haven't changed or are gone. A cascade kill writes state once, and kill
  and clean-up no longer freeze the TUI.
