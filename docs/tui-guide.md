# TUI Guide

Complete guide to the overcode terminal user interface.

## Overview

The TUI provides a real-time dashboard for monitoring and controlling your Claude Code agents. Launch it with:

```bash
overcode monitor      # Standalone monitor
overcode supervisor   # Monitor with supervisor daemon
```

## Display Modes

### Tree View (Default)
Shows agents in a hierarchical list with expandable details. Press `space` to expand/collapse individual agents, or `e` to expand/collapse all.

### List + Preview Mode
Press `m` to toggle. Shows a compact agent list on the left with a live terminal preview on the right. The preview updates in real-time as the selected agent works.

## Keyboard Shortcuts

### Navigation

| Key | Action |
|-----|--------|
| `j` / `↓` | Move to next agent |
| `k` / `↑` | Move to previous agent |
| `b` | Jump to next agent needing attention |
| `space` | Toggle expand/collapse focused agent |
| `e` | Expand/collapse all agents |

### View Controls

| Key | Action |
|-----|--------|
| `m` | Toggle tree / list+preview mode |
| `t` | Toggle timeline display |
| `d` | Toggle daemon log panel |
| `g` | Show/hide terminated agents |
| `Z` | Show/hide sleeping agents |
| `h` / `?` | Show/hide help overlay |

### Display Customization

| Key | Action |
|-----|--------|
| `s` | Cycle summary detail: low → med → full |
| `l` | Cycle summary content: AI short → AI long → orders → annotation |
| `v` | Cycle detail lines: 5 → 10 → 20 → 50 |
| `S` | Cycle sort mode: alphabetical → status → value |
| `$` | Toggle cost display (tokens vs dollars) |
| `M` | Toggle monochrome mode |

### Agent Control

| Key | Action |
|-----|--------|
| `i` / `:` | Open command bar to send instruction |
| `o` | Set standing orders |
| `I` | Edit human annotation |
| `Enter` | Send Enter to agent (approve prompts) |
| `1-5` | Send numbered option to agent |
| `n` | Create new agent |
| `R` | Restart agent (double-press to confirm) |
| `x` | Kill agent (double-press to confirm) |
| `z` | Toggle sleep mode |
| `V` | Edit agent priority value |
| `c` | Sync to main branch (double-press to confirm) |
| `H` | Prepare all for handover (double-press to confirm) |

### Daemon Control

| Key | Action |
|-----|--------|
| `[` | Start supervisor daemon |
| `]` | Stop supervisor daemon |
| `\` | Restart monitor daemon |
| `w` | Toggle web dashboard server |
| `a` | Toggle AI summarizer |

### Utility

| Key | Action |
|-----|--------|
| `p` | Toggle tmux pane sync |
| `y` | Toggle copy mode (disable mouse for text selection) |
| `r` | Force manual refresh |
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
| `Ctrl+Enter` | Send (in multi-line mode) |
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

### Bell Indicator

A bell icon (🔔) appears on agents that have stalled and haven't been visited yet. Focus the agent to clear the bell. Press `b` to jump directly to agents with bells.

## Summary Content Modes

Press `l` to cycle through what's shown in the summary line:

1. **AI Short** (💬) - Brief AI-generated summary of current activity
2. **AI Long** (📖) - Detailed AI summary with broader context
3. **Orders** (🎯) - Standing orders for this agent
4. **Annotation** (✏️) - Human-written notes

AI summaries require the summarizer to be enabled (press `a`) and configured with an API key.

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

## Split-Screen Setup

For the best monitoring experience with live agent output:

### iTerm2 (Recommended)
1. Open iTerm2
2. `Cmd+Shift+D` to split horizontally
3. In the top pane: `overcode monitor`
4. In the bottom pane: `tmux attach -t agents`
5. Press `p` in the monitor to enable pane sync
6. Use `j/k` to navigate—the bottom pane follows your selection

### Other Terminals
1. Split your terminal horizontally
2. Run `overcode monitor` in one pane
3. Run `tmux attach -t agents` in the other
4. Enable pane sync with `p`

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
