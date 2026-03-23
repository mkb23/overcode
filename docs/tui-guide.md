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

When using "Tree" sort mode (`S`), agents display in a parent/child hierarchy with tree connectors (├─/└─). Press `X` to collapse/expand a parent's children. The child count column (👶) shows direct children per agent.

## Keyboard Shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `j` / `↓` | Move to next agent |
| `k` / `↑` | Move to previous agent |
| `b` | Jump to next agent needing attention |

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
| `S` | Cycle sort mode: alphabetical → status → value → tree |
| `$` | Cycle cost display (tokens / dollars / joules) |
| `C` | Open column configuration |
| `L` | Toggle column headers |
| `M` | Toggle monochrome mode |
| `E` | Toggle emoji-free mode |

### Agent Control

| Key | Action |
|-----|--------|
| `i` / `:` | Open command bar to send instruction |
| `o` | Set standing orders |
| `a` | Edit human annotation |
| `I` | Browse instruction history |
| `Enter` | Send Enter to agent (approve prompts) |
| `1-5` | Send numbered option to agent |
| `n` | Create new agent |
| `N` | Create new remote agent (on sister) |
| `F` | Fork agent (with conversation context) |
| `R` | Restart agent (double-press to confirm) |
| `x` | Kill agent (double-press to confirm) |
| `z` | Toggle sleep mode |
| `V` | Edit agent priority value |
| `B` | Edit cost budget |
| `c` | Sync to main + clear (double-press to confirm) |
| `T` | Handover all (double-press to confirm) |
| `H` | Configure heartbeat |
| `K` | Toggle hook-based status detection |
| `Ctrl+T` | Toggle time context |
| `G` | New agent defaults |
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
| `PageUp/Down` | Terminal pane | Enter scrollback mode |
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

The TUI captures mouse events for interaction. To select and copy text:

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
3. Press `S` to cycle to "value" sort mode
4. Agents with higher values appear first

You can also sort by status (stalled agents first) or alphabetically.

## Tips

- **Quick attention**: Press `b` repeatedly to cycle through all agents needing attention
- **Approve quickly**: `Enter` sends Enter to approve permission prompts
- **Numbered options**: Press `1-5` to quickly select menu options
- **Bulk sleep**: Use `z` to sleep agents you're not actively using—they won't count toward stats
- **Monitor remotely**: Press `w` to start the web server, then access from your phone
