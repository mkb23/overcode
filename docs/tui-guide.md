# TUI Guide

Complete guide to the overcode terminal user interface.

## Overview

The TUI provides a real-time dashboard for monitoring and controlling your Claude Code agents. Launch it with:

```bash
overcode monitor      # Standalone monitor
overcode supervisor   # Monitor with supervisor daemon
```

## Display Modes

### Agent List
Shows all agents as single-line summaries with live status, metrics, and a content area. Press `m` to toggle the preview pane, which shows the focused agent's terminal output below the list.

When using "Tree" sort order (`S`, then pick Tree), agents display in a parent/child hierarchy with tree connectors (├─/└─). Press `X` to collapse/expand a parent's children; folds are remembered per session in `tui_preferences.json`, so a parent you folded stays folded after restarting the TUI or the tmux session (#464). The child count column (👶) shows direct children per agent.

### Sorting and Column Headers

Click any column header to sort by that column; click it again to reverse. The sorted column is drawn bold with ▼ (largest first) or ▲ (smallest / A→Z first). Numbers sort largest first by default, text A→Z; agents with no value for the column (e.g. no CPU sample yet) always go last. Child agents stay grouped under their parent in every sort.

From the keyboard, `S` opens the command palette on sort choices: every sortable column plus Tree order. Type a few letters of the column's name or its header code (`cpu`, `tok`, `git`) and press `Enter`; choosing the current sort again reverses it, and `Tab` does the same while keeping the picker open. "Reverse sort" in the `/` palette flips the current direction. The sort is saved in `tui_preferences.json`.

Hover a column header to see what the column means. The column configurator (`C`) is the full guide: every column is listed under its group with its header code, what it shows for the focused agent, and what it means, and the foot explains the highlighted one — its default detail levels and whether it sorts. It sits at the bottom of the screen so the first agents stay in view as you toggle columns. `L` hides or shows the header row.

In sorts driven by live data (status, value, CPU, time in state…) the list re-orders itself as agents change. The highlight follows the *agent*, not the row, so the selected agent — and the tmux pane synced to it — stay the same when a row moves (#471).

## Keyboard Shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `j` / `↓` | Move to next agent |
| `k` / `↑` | Move to previous agent |
| `b` | Jump to next agent needing attention |
| `Ctrl+P` | Jump to an agent by name (fuzzy search) |
| `J` | Switch between the agents and jobs views |
| `e` | Open the [overagent](#the-overagent): ask overcode for help, advice or changes |
| `u` | [Your journey](#your-journey): what you've found in overcode, and what's next |

### View Controls

| Key | Action |
|-----|--------|
| `m` | Toggle preview pane |
| `t` | Toggle timeline display |
| `d` | Toggle daemon log panel |
| `g` | Show/hide terminated agents |
| `Z` | Show/hide sleeping agents |
| `D` | Show/hide done child agents |
| `X` | Collapse/expand children (tree mode) |
| `h` / `?` | Show/hide help overlay |

### Display Customization

| Key | Action |
|-----|--------|
| `s` | Cycle summary detail: low → med → high → full |
| `l` | Cycle summary content: AI short → AI long → orders → annotation → heartbeat |
| `S` | Sort by any column or tree order (picker; choosing the current sort reverses it) |
| `$` | Cycle cost display (tokens / dollars / joules) |
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
| `Ctrl+N` | Rename agent (keeps its conversation; the old name stays an alias) |
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

### Daemon Control

| Key | Action |
|-----|--------|
| `[` | Start supervisor daemon |
| `]` | Stop supervisor daemon |
| `\` | Restart monitor daemon |
| `w` | Toggle web dashboard server |
| `A` | Toggle AI summarizer |

### Utility

| Key | Action |
|-----|--------|
| `p` | Pause/resume heartbeat |
| `P` | Toggle tmux pane sync |
| `y` | Toggle copy mode (disable mouse for text selection) |
| `r` | Resize focused agent's tmux pane |
| `J` | Toggle jobs mode |
| `,` | Move timeline baseline back 15 minutes |
| `.` | Move timeline baseline forward 15 minutes |
| `0` | Reset timeline baseline to now |
| `q` | Quit |

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

Agents display colored status indicators:

| Color | Status | Meaning |
|-------|--------|---------|
| Green | Running | Claude is actively working |
| Yellow | No Orders | Waiting for standing orders |
| Orange | Wait Supervisor | Waiting for supervisor approval |
| Red | Wait User | Waiting for human input |
| Grey | Asleep | Agent is paused (sleep mode) |
| Black | Terminated | Tmux window no longer exists |
| Green ✓ | Done | Child agent completed its task |

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

### Baseline Adjustment

The timeline can show a "mean spin" efficiency metric. Adjust the baseline with:
- `,` - Move baseline back 15 minutes
- `.` - Move baseline forward 15 minutes
- `0` - Reset to instantaneous (no baseline)

This helps compare current activity against a past baseline, useful for ignoring breaks or meetings.

## Daemon Panel

Press `d` to show the daemon log panel at the bottom. This displays:
- Monitor daemon status and logs
- Supervisor daemon activity
- Web server URL when running
- Recent interventions and decisions

## Tmux Split Layout

The recommended way to use overcode. Run `overcode tmux` to get a two-pane layout with the dashboard on top and the focused agent's live terminal on the bottom.

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
| `q` | Dashboard | Detach (return to previous tmux session) |

### Sister Agents in Split Mode

When you navigate to a remote/sister agent, the dashboard automatically zooms to show a preview pane with the sister's terminal content (polled every 1.5 seconds). Navigate back to a local agent to restore the normal split layout.

### Manual Split Setup (Alternative)

If you prefer not to use `overcode tmux`, you can set up a split manually:
1. Split your terminal horizontally
2. Run `overcode monitor` in the top pane
3. Run `tmux attach -t agents` in the bottom pane
4. Press `p` to enable pane sync

## Copy Mode

The TUI captures mouse events for interaction (row selection, header click-to-sort and tooltips, preview scrolling). To select and copy text:

1. Press `y` to enter copy mode (disables mouse capture)
2. Select text with your mouse
3. Copy with `Cmd+C` (macOS) or `Ctrl+Shift+C` (Linux)
4. Press `y` again to exit copy mode

## Monochrome Mode

If you experience color rendering issues in your terminal, press `M` to toggle monochrome mode. This strips ANSI color codes from the preview pane.

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
- **Monitor remotely**: Press `w` to start the web server, then access from your phone
