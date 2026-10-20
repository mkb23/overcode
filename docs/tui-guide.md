# TUI Guide

Complete guide to the overcode terminal user interface.

## Overview

The TUI provides a real-time dashboard for monitoring and controlling your coding agents. It runs as the top pane of a tmux split, with the focused agent's live terminal below it (see [Tmux Split Layout](#tmux-split-layout)). Launch it with any of:

```bash
overcode            # the same as `overcode tmux`
overcode tmux
overcode monitor    # an alias, kept for old habits and scripts
```

## Display Modes

### Agent List
Shows all agents as single-line summaries with live status, metrics, and a content area. The focused agent's terminal is the bottom pane of the split. A sister agent (one on another machine) has no local terminal, so selecting one zooms the dashboard and shows a preview pane of its polled output instead; jobs view (`J`) does the same for the focused job.

When using "Tree" sort order (`S`, then pick Tree), agents display in a parent/child hierarchy with tree connectors (├─/└─). Press `←` on a parent to fold its children and `→` to unfold them (`←` on a child moves to its parent); `X` folds or unfolds every parent at once. Folds are remembered per session in `tui_preferences.json`, so a parent you folded stays folded after restarting the TUI or the tmux session (#464). The child count column (👶) shows direct children per agent.

The agent you last moved the highlight to is remembered the same way: quit and reopen `overcode tmux` and the highlight comes back on it, with the bottom pane on its window. If that agent has gone, the first agent is highlighted.

### Delegated work (👥, 🧩)

A Claude agent can hand work out inside its own session: subagents
(background by default), forks, workflows and teammates. None of them is an
overcode agent; the SubAgents column (`SUBA`) rolls them up on the
parent's row, e.g. `▸👥 ▶15 🟡2 🟠1 ✅26 ❌1 🧩1`: how many are working (▶),
waiting on background work of their own (🟡, as on their rows), waiting for
approval (🟠), idle (⚪, teammates listening), finished, failed, and
workflow runs. Only non-zero counts show and there is no total; hover the
cell for it. An agent with no hook counts (no hooks installed, or a sister
on an older overcode) still shows the subagents its transcript files say are
running, e.g. ` 👥 ▶2`, without the fold marker. The column appears once any
agent delegates, and replaces 0.6.0's DLG and SUB columns: saved column
settings naming `swarm` or `subagent_count` carry over to it.

Press `→` on that row to expand it: its delegated agents appear as
read-only rows right under it, and the fold marker turns to `▾`. The rows
sit on the same column grid as the agent rows, cell for cell (trimmed
here to status, time, SubAgents, NAME, TYPE, TOK, $ and MDL):

```
🟢 10.0d ▾👥 ▶1 🟡1 🟠1 ✅6 ❌1 🧩1→ bravo                   agent               Σ  1.5K  $0.50  Op4.6 │ 💬 bravo working
🟠   20s                          -  ├─🧩 verify-1           review-060 · Verify Σ  9.0K  $1.25  Op4.6 │ 💬 needs approval in the parent's pane
🟡  5.0m                          -  ├─👥 Fix the done glyph general-purpose     Σ940.3K  $2.10  Op4.6 │ 💬 waiting on its own background work
🟢   30s                          -  └─👥 map the engine     Explore             Σ 15.3K  $0.50  Hk4.5 │ 💬 Read src/engine.py
```

No group or phase lines: the agent's row already sums up its delegated
work, so the lines under it are the agents themselves, and what has
finished is its ✅/❌ counts. With one type of agent the TYPE column
takes no width at all:

```
🟢 10.0d ▾👥 🟡2 ✅15              → charlie              Σ  1.5K  $0.50  Op4.6 │ 💬 charlie working
🟡  1.4h                          -  ├─👥 general-purpose  Σ 13.1M  $5.60  Op4.6 │ 💬 waiting on its own background work
🟡  1.2h                          -  └─👥 general-purpose  Σ 16.1M  $6.90  Op4.6 │ 💬 waiting on its own background work
```

- Live agents come first: 🟠 (waiting for your approval in the parent's
  pane) at the top, then the rest by when they started, oldest first.
  One that finished moments ago stays a while (below).
- TYPE says what kind of agent each is: its subagent type
  (`general-purpose`, `Explore`, …), `fork`, `teammate`, or the
  workflow's name with its phase when that fits (`review-060 · Verify`;
  otherwise the phase leads the summary text). It shows only while the
  expanded rows on screen hold two or more types; agent rows then show
  their own kind (agent / child / bg), dim.
- Each row fills the columns it has a value for: its light, time in state
  (since it started; its run time once finished), RES (✅ / ❌, hover for
  the result text), NAME (tree connector, 👥 or 🧩 for a workflow agent,
  title), TYPE, MDL, TOK, $, Q% and uptime. TOK, $ and Q% are its own
  spend, read from its transcript at the stats cadence (Q% is its whole
  run's points of the weekly quota, with a subscription). MDL is its own
  model, or the parent's drawn dim when it inherits it. BILL shows the
  parent's code, dim: its spend is billed there. Every other column shows
  a dim `-` where its values end, so nothing of the parent's (repo,
  branch, diff, context, mode, …) is repeated. The NAME column widens for
  the titles as it would for a longer agent name. MDL still hides while
  every agent runs the same model.
- The summary column follows `l`. A member shows, per mode: **AI short**
  what it is doing (its last tool call, or "needs approval in the parent's
  pane", "idle, listening", "waiting on its own background work"; once
  finished, its result and run time); **AI context** its agent type, kind
  and phase, then the same; **standing orders** its task as given (the full
  title); **last command** its last tool call; **annotation** and
  **heartbeat** `-`. A summary line (`+k more`) shows its own text in every
  mode.
- The KIND column (hidden by default; switch it on with `C`) spells out
  agent / child for fleet rows, and sub / fork / team / wf for delegated
  work.
- More than 8 live agents under one parent: it opens with its top three
  "needs you" (🟠 first, then 🟢, 🟡, ⚪, oldest first) and `+k more`;
  `→` on that line lists them all. Collapsing the agent (`←` on its row)
  makes the list compact again for next time.
- `j`/`k` move the highlight through the rows and on to the next agent. The
  parent keeps focus, so the pane below stays on the parent: a virtual row
  has no terminal of its own.
- `→` on a member opens a detail line under the NAME column (model, type,
  phase, start time, tokens, cost, last tool, result). `←` closes what the
  highlight is on, or goes back to the agent's row; on the agent's own row
  it collapses the whole thing.
- Virtual rows are read only. With the highlight on one, the agent keys
  (send, kill, restart, rename, …) say so and do nothing; `Enter` moves to
  the parent's pane, where a subagent's permission prompt is answered.
- A finished member stays (dimmed, ✅/❌) for 30 seconds, then is only a
  count in the agent's SubAgents cell
  (`observability.finished_row_seconds` in config.yaml).
  Subagents never ring the bell: their permission prompt already turns
  the parent 🟠.
- `f` cycles which agents' delegated work opens by itself: **off** (the
  default: only what you open with `→`), **groups** (every agent with
  delegated work in flight; it closes again when the work is done) and
  **needs you** (only agents with a subagent waiting for approval, listing
  just those until you open `+k more`). `→` / `←` on an agent still
  override the rule for that agent; `f` starts afresh, dropping those
  choices.
- `d` (or **Agent visibility…** in the `/` palette) opens one dialog
  for what the table shows. **Agents**: killed agents (ghost rows),
  sleeping agents, done child agents. **Subagents**:
  the `f` rule; what finished subagents show as (**show**: their row for
  a while and their ✅/❌ count; **failures only**: just the ❌ ones;
  **hide**: live work only); how long a finished subagent keeps its row
  (0s, 10s, 30s, 2m, saved in config.yaml). Changes apply as you make
  them; `Esc` closes it.
- `X` expands every agent that has delegated work (and, in tree sort,
  unfolds every parent's children); with something open, it collapses
  everything instead. It does not change the `f` rule.
- What is open (agents, opened `+k more` lists, and the `f`
  rule) is remembered in `tui_preferences.json`, so it comes back after
  restarting the TUI. Entries for agents that are gone are dropped, and so
  are the group, phase and done-line entries earlier 0.6.1 builds saved.
- Hover the parent's tokens or $ cell for its spend split: its own turns
  and its delegated work (which is inside the total, never added again).
- Only expanded parents cost anything: the engine reads the subagents'
  meta files and workflow journals, and sends their rows, for those alone.
- The timeline (`t`) draws a density strip of the delegated work under the
  agent, unfolded per group while the agent is expanded here (see
  "Delegated work in the timeline").

### Claude background sessions (🌙)

A Claude background session (`claude --bg`, `/bg` or `←` in Claude, or one
dispatched from `claude agents`) runs under Claude's own supervisor, not
overcode's. When its directory is inside one of your agents' directories, or
in another git worktree of a repository one of them works in, it gets a row
of its own, marked 🌙 (`Bs` emoji-free), right under the agent it came from:

```
🟡 bravo            opus   Waiting on 2 background agents            $4.20
🔴 └─🌙 migrate-db  haiku  Allow rm -rf build?                       $0.31
```

- It is nested under the agent whose hooks it ran under (it inherited that
  agent's environment, as `/bg` from the agent's Claude does), or whose
  conversation it continues, or else whose directory holds it (the deepest
  one). A session in a worktree outside every agent's directory nests under
  the agent working in that worktree's repository: the one in the main
  checkout, else the one with the shortest directory path, then by name. A
  session in none of these is not shown. KIND says `bg`.
- Its light comes from Claude's own record of it: 🟢 working, 🟡 waiting on
  its own background work, 🔴 blocked on a question or permission (the
  status text is what it asks), ⚪ finished with a Result (✅ done, ❌
  failed, 📭 stopped). A blocked session rings the bell, once per stretch.
- A finished session keeps its row for 30 seconds
  (`observability.finished_row_seconds`), then the row goes.
- Its tokens and $ are its own conversation's, read at the stats cadence
  (every 5 s while you watch) while it runs, and count once in the totals;
  they are never added to the agent it is nested under. Its spend is billed
  to that agent's billing code: `overcode billing` counts it among the
  code's agents and in a `background` column (see
  [Billing Codes](configuration.md#billing-codes)).
- Only Claude's own actions work on it. `Enter` attaches: `claude attach
  <id>` opens in the pane below (leave it with Claude's `←` or `Ctrl+Z`).
  `x` stops it after a second press (`claude stop`). "Remove background
  session" in the `/` palette deletes it (`claude rm`), also confirmed.
  Every other agent key says so and does nothing.
- Selecting the row shows its attach window if one is open, else a short
  note about these keys.
- `observability.background_sessions: false` in config.yaml turns the rows
  off.

### Sorting and Column Headers

Click any column header to sort by that column; click it again to reverse. The sorted column is drawn bold with ▼ (largest first) or ▲ (smallest / A→Z first). Numbers sort largest first by default, text A→Z; agents with no value for the column (e.g. no CPU sample yet) always go last. Child agents stay grouped under their parent in every sort.

Click an agent's row to select it, just like moving to it with `j`/`k`: the tmux pane below switches to that agent. Hover a row's emoji cells (skills, PRF, tools, status detail) to see what each emoji means.

### A conversation overcode did not start (🔀)

Only an agent's own Claude moves its status. A Claude the agent starts
itself (`claude -p` in its Bash tool inherits the agent's overcode
environment) is told apart by its process, and its turns never change the
agent's light, obligations or 👥 list. When a conversation overcode never
saw does run in an agent's name, the agent shows 🔀 (`Cv` emoji-free) in its
status detail for 30 minutes, beside its own light; hover it for which one:

- **resumed a conversation overcode did not start; now tracked as this
  agent's**: you `/resume`d into it in the agent's own pane (or started
  `claude` by hand there after the agent's exited). It is the agent: its
  turns drive the row, and `restart` resumes it.
- **in a conversation another agent owns**: the same, into another live
  agent's conversation. The row follows it, but the record stays on the old
  conversation, so `restart` returns there.
- **a conversation overcode can't place ran in this agent's name; its events
  are ignored**: neither the agent's own Claude nor one it started.

`overcode doctor` lists the same warning for a day.

From the keyboard, `S` opens the command palette on sort choices: every sortable column plus Tree order. Type a few letters of the column's name or its header code (`cpu`, `tok`, `git`) and press `Enter`; choosing the current sort again reverses it, and `Tab` does the same while keeping the picker open. "Reverse sort" in the `/` palette flips the current direction. The sort is saved in `tui_preferences.json`.

Hover a column header to see what the column means. The column configurator (`C`) is the full guide: every column is listed under its group with its header code, what it shows for the focused agent, and what it means, and the foot explains the highlighted one — its default detail levels and whether it sorts. It sits at the bottom of the screen so the first agents stay in view as you toggle columns. `L` hides or shows the header row.

In sorts driven by live data (status, value, CPU, time in state…) the list re-orders itself as agents change. The highlight follows the *agent*, not the row, so the selected agent — and the tmux pane synced to it — stay the same when a row moves (#471).

## Keyboard Shortcuts

These are the default keys. Any of them can be remapped, or switched to the VSCode-safe preset, under `keys:` in config.yaml — see [Configuration → Keybindings](configuration.md#keybindings). The help overlay (`h`/`?`) always shows the keys in effect, grouped like the command palette, in one to three columns depending on terminal width; scroll it with `↑`/`↓`, `PgUp`/`PgDn`, `Home`/`End`.

### Navigation

| Key | Action |
|-----|--------|
| `j` / `↓` | Move to next agent |
| `k` / `↑` | Move to previous agent |
| `→` / `←` | Expand / collapse the row: an agent's delegated work, one level at a time ([above](#delegated-work--)), and its children in tree sort |
| `X` | Expand / collapse all (delegated work, and children in tree sort) |
| `f` | Cycle which agents' delegated work opens by itself: off → groups → needs you |
| `b` | Jump to next agent needing attention |
| `Ctrl+P` | Jump to an agent by name (fuzzy search) |
| `J` | Switch between the agents and jobs views |
| `e` | Open the [overagent](#the-overagent): ask overcode for help, advice or changes |
| `u` | [Your journey](#your-journey): what you've found in overcode, and what's next |

### View Controls

| Key | Action |
|-----|--------|
| `t` | Toggle timeline display (hidden at start: the top pane is short) |
| `d` | Agent visibility: killed, sleeping and done agents; subagent options |
| `Z` | Show/hide sleeping agents |
| `h` / `?` | Show/hide help overlay |

The daemon log panel and the TUI diagnostic log are in the `/` palette
(no key).

### Display Customization

| Key | Action |
|-----|--------|
| `s` | Cycle summary detail: low → med → high → full |
| `l` | Cycle summary content: AI short → AI long → orders → annotation → heartbeat |
| `S` | Sort by any column or tree order (picker; choosing the current sort reverses it) |
| `$` | Cycle cost display (tokens / dollars / energy: joules, with the burn rate in watts / weekly quota on a subscription) |
| `C` | Open column configuration |
| `L` | Toggle column headers |
| `M` | Toggle monochrome mode |
| `E` | Toggle emoji-free mode |

### Agent Control

Some actions depend on what the agent's backend supports — see the
[feature support table](backends.md#feature-support-at-a-glance) in the
Backends guide. Unsupported actions are grayed out for that agent.

| Key | Action |
|-----|--------|
| `i` / `:` | Open command bar to send instruction |
| `o` | Set standing orders |
| `a` | Edit human annotation |
| `I` | Browse instruction history |
| `Enter` | Send Enter to agent (approve prompts) |
| `1-5` | Send numbered option to agent |
| `n` | Create new agent (Host field picks local or a remote sister) |
| `F` | Fork agent (with conversation context) |
| `Ctrl+N` | Rename agent (it keeps running, even mid-turn; the old name stays an alias), and choose who names it: you or overcode (see [Agent names](#agent-names)) |
| `Ctrl+R` | Cycle the focused agent's focal repo (multi-repo workspaces) |
| `R` | Restart agent (double-press to confirm) |
| `x` | Kill agent (double-press to confirm) |
| `z` | Toggle sleep mode |
| `V` | Edit agent priority value |
| `B` | Edit cost budget |
| `c` | Sync to main + clear (double-press to confirm) |
| `T` | Filter agents by tag |
| `H` | Configure heartbeat |
| `K` | Toggle detection mode (hooks/polling) |
| `Ctrl+T` | Toggle time context |
| `G` | New agent defaults |
| `W` | Skill profiles: build named sets of skills, pin them to folders ([guide](skill-profiles.md)) |
| `U` | Sister visibility |
| `/` → "Billing code…" | Bill the agent's spend to another code from now on (no default key; see [Billing codes](#billing-codes)) |

### Daemon Control

| Key | Action |
|-----|--------|
| `[` | Start supervisor daemon |
| `]` | Stop supervisor daemon |
| `\` | Restart monitor daemon |
| `w` | Toggle the API server (sister API) |
| `A` | Toggle AI summarizer |

### Utility

| Key | Action |
|-----|--------|
| `p` | Pause/resume heartbeat |
| `y` | Toggle copy mode (disable mouse for text selection) |
| `r` | Resize focused agent's tmux window to the bottom pane |
| `J` | Toggle jobs mode |
| `,` | Move timeline baseline back 15 minutes |
| `.` | Move timeline baseline forward 15 minutes |
| `0` | Reset timeline baseline to now |
| `q` | Quit |

### Billing codes

Every agent bills its spend to a code — its name unless chosen otherwise
(see [Billing Codes](configuration.md#billing-codes)). The TUI sets it in
two places, both cycling with `Space` through the agent's own name (shown
as `(name)`) and then the `billing.codes` shortlist from config.yaml:

```yaml
billing:
  codes: [client-a, client-b, internal]
```

- **New agent** (`n`): the Billing row. `Space` cycles, `Enter` types a
  code of your own. (A child always takes its parent's code; a dialog
  opened for a child shows that code, locked.)
- **An existing agent**: `/`, then "Billing code…". The dialog opens on
  its current code, ready to type; `Space` cycles, `Enter` applies, `Esc`
  cancels. Applying it is `overcode rebill`: forward only — the spend so
  far stays on the old code. Only this agent moves: children already
  running keep their codes, children it launches later take the new one.
  A sister's agent has to be rebilled on its own machine.

The Billing column (BILL; hidden below full detail, shown by
`overcode list`) shows each agent's code by its current name, dim while it
is still the agent's name; after `overcode billing rename` it shows the new
name within a daemon pass. Codes are typed and shown by name; a code's
previous name still picks it. A bad `billing:` entry is skipped and the
dialog says so.

### Agent names

The NAME column marks names nobody chose:

- **dim**: a default name, made up by overcode: the new-agent dialog's
  directory name left as it was, or a fork's `<name>-fork`.
- **`~name`**: overcode named the agent (auto-naming, `naming.enabled`).

A name you typed (in the new-agent dialog, `Ctrl+N`, or `-n` in a shell)
or one a parent agent gave its child is plain, and overcode never
renames it. The `~` takes a cell from the name, so the column keeps its
width. Hover the name to see who named it, whether it was launched with
`--force-name`, and the names it had before.

- `Ctrl+N` renames the agent; its **Named by** row says who names it from
  then on: `you` (locked) or `overcode`. A name you type switches it to
  `you`; set it back to `overcode` to keep auto-naming. Changing only the
  row, with the name as it was, just hands the name over (or takes it back).
- In the palette (`/`): "Let overcode name it" and "Keep this name" do the
  same for the focused agent (`overcode rename <agent> --auto` / `--lock`).

With auto-naming on (`naming.enabled`, see
[Naming](configuration.md#naming)), the engine names an agent once its
task is clear, at a turn end, and at most once more later. A toast says so
(`overcode-main2 → jwt-refresh (auto)`), the name gets its `~`, and the
daemon panel (`d`) logs it with the model's reason. The agent keeps
running; its old name still reaches it. To undo, `Ctrl+N` it back: the name
is then yours and is not renamed again. No rename happens within 30 s of a
key pressed in the TUI, so a name does not change under you as you type it.
For a Claude Code agent the name also shows in Claude's own prompt box,
and a `/rename` typed inside Claude renames the agent.

### Weekly quota (subscriptions)

On a Claude subscription, `$` has a **weekly quota** mode that hides the
token, dollar and energy columns and shows the `Q%` column: each agent's
estimated share of the weekly quota this week, in percentage points with
two decimals (`1.37%`; `-` for an agent off the subscription). It is a
prediction from the agent's tokens, because Claude reports the window only
in whole percents; the cell is dim until the model has been fitted to your
account. Hover it for the agent's share of the current 5-hour window, the
account's own weekly and 5-hour percentages and the model's confidence. In
this mode the status bar's Σ sums the agents' points; the Usage section
shows the account's percentages as the engine last read them. The column
can also be switched on in the column configurator (`C`), and never shows
for a fleet with no subscription agent. See
[Subscription Quota](configuration.md#subscription-quota) and
`overcode usage model`.

## Command Bar

Press `i` or `:` to open the command bar for sending instructions to the selected agent.

| Key | Action |
|-----|--------|
| `Enter` | Send instruction |
| `Ctrl+E` | Toggle multi-line editing |
| `Ctrl+S` / `Ctrl+Enter` | Send (in multi-line mode) |
| `Ctrl+O` | Set as standing order instead of sending |
| `Escape` | Clear and close command bar |

## Status Indicators

The traffic light answers one question: what is the agent doing, and does it
need you?

| Light | Name | Meaning |
|-------|------|---------|
| 🟢 | Working | Generating or running a tool |
| 🟡 | Waiting on something automatic | Background work, a wakeup or child agents will resume it |
| 🟠 | Needs approval | A permission prompt or a plan to approve |
| 🔴 | Needs input | Its turn is over and it waits for you |
| ⚪ | At rest | A child agent that stopped, reported or not |
| ❔ | Unknown | Starting up, or its status cannot be read |
| ⚫ | Gone | The agent exited or its window is gone |

Shades of those: 💚 working (a heartbeat started this turn), 💛 waiting on a
heartbeat that will re-prompt it, 🟣 an API or hook error needs attention,
💤 asleep (paused by you, left out of stats).

A parent whose own turn ended while any of its children still work reads 🟡,
with 👶 in the status detail ("Waiting on 2 children"). Once every child is at
rest, the usual rules apply: 🔴 if it waits for you.

### Result column

How a child's task went is the Result column (RES), not the light:

| Result | Meaning |
|--------|---------|
| 📭 | Stopped without a report |
| ✅ | Reported success (with its reason) |
| ❌ | Reported failure (with its reason) |
| ⌛ | Oversight timeout expired |

The cell is blank until a child has a result, and the reason is cut to fit;
hover it for the full reason (`overcode list` prints it in full after the
activity). The 👶 column rolls a parent's children up the same way, e.g.
`👶 5 ▶2 📭1 ✅1 ❌1` (only non-zero counts: ▶ still working). Unlike
SubAgents it keeps the total: the states overlap and don't cover every
child.

### Mode column

The Mode column (MOD; hidden while every agent shows the same mode) is the
agent's permission mode. The first three are how it was launched; the
others are what you switched a Claude agent to with `shift+tab`, read from
its status bar:

| Mode | Emoji-free | Meaning |
|------|------------|---------|
| 👮 | `N:` | Normal: asks before edits and commands |
| 🏃 | `P>` | Permissive (Claude: don't ask) |
| 🔥 | `B!` | Bypass: every permission check skipped |
| ⚡ | `>>` | Accept edits: edits go through, commands still ask |
| 🧠 | `A~` | Auto mode: a classifier decides what to allow |
| 🧭 | `P?` | Plan mode: read-only until the plan is approved |

Plan mode shows whatever the launch mode. Accept edits and auto mode show
over a normal launch; a permissive or bypass agent keeps 🏃 / 🔥. Hover the
cell for the mode and, when switched, how it was launched.

### Bell Indicator

A bell icon (🔔) appears on agents that have stalled and haven't been visited yet. Focus the agent to clear the bell. Press `b` to jump directly to agents with bells.

## Summary Content Modes

Press `l` to cycle through what's shown in the summary line:

1. **AI Short** (💬) - Brief AI-generated summary of current activity
2. **AI Long** (📖) - Detailed AI summary with broader context
3. **Orders** (🎯) - Standing orders for this agent
4. **Annotation** (✏️) - Human-written notes
5. **Heartbeat** - Latest heartbeat data

AI summaries require the summarizer to be enabled (press `A`) and configured with an API key.

## Timeline

Press `t` to show a timeline visualization of agent status over time. Each agent shows a bar representing the last few hours of activity:

- **Green** - Running/working
- **Yellow/Orange/Red** - Various waiting states
- **Grey hatching** - Sleeping

🌙 background sessions get a row of their own like any agent, for as long
as their list row is shown.

### Delegated work in the timeline

An agent that delegated work (a 👥 cell in the list) gets a density strip
right under its timeline row:

```
  review-060        ████████▓▓▓▓▓▓████████████████
  └ 👥 swarm          ▁▂▅▇██▇▆▆▇█▇▅▃▂▁········ ↑20
  fix-flaky         ██████████████████████████████
```

- **Height** is how many of its subagents, workflow agents and teammates
  were live, as the mean over each slot's time, scaled to the strip's own
  peak. `↑20` at the end is that peak (the most live at once in the
  window). `·` means nothing was live; a blank, no record.
- **Colour** is the worst light in the slot: orange (a permission prompt)
  over green (working) over yellow (waiting on its own background work).
- A slot where a member failed is **red and underlined**.
- The strips fold with the list: expanding the agent with `→` replaces
  the one strip with one per group (each workflow run, the team, loose
  subagents), in the list's order; `←` folds them back.
- Only groups with something live inside the window get a strip.

The engine writes these counts to `swarm_history.csv` (beside
`agent_status_history.csv`) only when they change, plus one row every
5 minutes while something is live; the file is rotated and compressed with
the status history.

### Baseline Adjustment

The timeline can show a "mean spin" efficiency metric. Adjust the baseline with:
- `,` - Move baseline back 15 minutes
- `.` - Move baseline forward 15 minutes
- `0` - Reset to instantaneous (no baseline)

This helps compare current activity against a past baseline, useful for ignoring breaks or meetings.

## Daemon Panel

Open the daemon log panel at the bottom from the `/` palette ("Daemon panel"). It displays:
- Monitor daemon status and logs
- Supervisor daemon activity
- API server port when running
- Recent interventions and decisions

## Tmux Split Layout

The only layout the dashboard has. `overcode tmux` (or plain `overcode`, or `overcode monitor`) gives a two-pane layout with the dashboard on top and the focused agent's live terminal on the bottom.

```bash
overcode tmux
```

### Split-Specific Controls

| Key | Where | Action |
|-----|-------|--------|
| `Tab` | Anywhere | Toggle focus between dashboard and terminal |
| `Option+J/K` | Terminal pane | Navigate agents without leaving the terminal |
| `PageUp/Down` | Terminal pane | Enter scrollback mode (full-screen agents such as opencode scroll their own transcript instead) |
| `=` / `-` | Dashboard | Grow / shrink dashboard pane |
| `q` | Dashboard | Stop the dashboard and detach (return to previous tmux session); the pane offers a one-key relaunch |

### Sister Agents in Split Mode

When you navigate to a remote/sister agent, the dashboard automatically zooms to show a preview pane with the sister's terminal content (polled every 1.5 seconds). Navigate back to a local agent to restore the normal split layout.

## Copy Mode

The TUI captures mouse events for interaction (row selection, header click-to-sort and tooltips). To select and copy text:

1. Press `y` to enter copy mode (disables mouse capture)
2. Select text with your mouse
3. Copy with `Cmd+C` (macOS) or `Ctrl+Shift+C` (Linux)
4. Press `y` again to exit copy mode

## Monochrome Mode

If you experience color rendering issues in your terminal, press `M` to toggle monochrome mode. This strips ANSI color codes from the preview pane (sister agents and jobs).

## Priority Sorting

Organize agents by priority:

1. Press `V` on an agent to set its priority value (default: 1000)
2. Higher values = higher priority
3. Press `S` and pick "Agent Value" (or click the `VAL` header)
4. Agents with higher values appear first

You can sort by any other column the same way — status (stalled agents first), name, cost, CPU and so on.

## Your Journey

Press `u` to see how much of overcode you have found. The panel shows:

- four tracks (Basics, Fleet, Oversight, Orchestration), each with a level
  bar and what's next
- the selected track's syllabus: ✓ done, → next, ◦ later, 🔒 needs another
  step first
- **the hard way**: things you do often but rarely by their key
- keys you press that do nothing
- every capability with how well you know it: ○ unaware, ◑ tried,
  ◐ habitual, ● fluent. A capability's key is shown only while you haven't
  used it.

`j`/`k` pick a track, `t` tries its next step, `a` asks the
[overagent](#the-overagent) to show you it, `esc` closes.
`overcode journey [--json]` prints the same thing.

It is worked out from the [usage log](configuration.md#usage-log) and from
what your agents show (a standing order set, an agent launched by an agent),
so something you set up another way still counts. Nothing about progress is
stored: turn the usage log off and the journey only sees the latter.
Unused capabilities slip back one level after `journey.decay_grace_days`
(30) and one more each `journey.decay_step_days` (45), never below "tried".

The panel never pops up by itself. If you'd like the occasional tip, turn
on the mentor (off by default):

```yaml
journey:
  mentor: occasional   # off (default) | occasional | coach
```

A tip replaces the footer line for up to 90 seconds, only when you've been
idle for 5 s, nothing is open and no agent is waiting on you. It suggests
one thing the journey shows you haven't found yet, or a key for something
you do the long way. `occasional` gives at most one per TUI run, 30 minutes
apart; `coach` up to four, 10 minutes apart. Doing what a tip suggests (or
opening `u`) makes tips a little more frequent. Letting one pass makes them
less frequent and snoozes that tip for a week. After your first month only
the single most useful tip is offered. New achievements get one toast each.
Achievements already earned when you first turn the mentor on are recorded
silently. The mentor's memory is `~/.overcode/journey_state.json`.

## The Overagent

Press `e` to talk to overcode itself. The overagent is a Claude Code agent
that knows overcode: ask it how to do something, why a column says `n/a`,
what you could be doing faster, or to change things ("hide cost and show
branch", "sort by status", "restart every dead agent").

It is an ordinary agent row, launched with the `overagent` backend the first
time you press `e`, in `~/.overcode`, named `overagent`. Its name is pink in
the list (underlined in monochrome) so it stands out from your agents. Later
presses focus it; in `overcode tmux` the bottom pane switches to it and takes
the keyboard.
You can also start one yourself: `overcode launch -n helper -B overagent`.

What it can do without asking: read overcode's state, change what the TUI
shows (`overcode view`, see the [CLI reference](cli-reference.md#view-commands)),
read your usage summary, and edit `~/.overcode/config.yaml`. Everything else
(kill, restart, launch, send, budgets) asks first, and it never runs with
permissions bypassed. It resolves "this" from what you have focused, and
when it suggests a key it lights it up in your TUI.

It always has the `overcode-configurator` skill, loaded as a plugin for its
own session only (`~/.overcode/overagent/plugin`). Nothing is added to
`~/.claude/skills`, so your other Claude sessions don't see it, and which
overcode skills you install globally (`overcode skills install`) stays up
to you.

## Tips

- **Quick attention**: Press `b` repeatedly to cycle through all agents needing attention
- **Approve quickly**: `Enter` sends Enter to approve permission prompts
- **Numbered options**: Press `1-5` to quickly select menu options
- **Bulk sleep**: Use `z` to sleep agents you're not actively using—they won't count toward stats
- **Watch from another machine**: Press `w` to start the API server, then add this machine as a sister on the other one
