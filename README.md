# overcode

A TUI supervisor for managing multiple Claude Code agents in tmux.

Monitor status, costs, and activity across all your agents from a single dashboard.

## Installation

```bash
pip install overcode
```

Requires: Python 3.12+, tmux, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)

## Quick Start

```bash
# Launch an agent
overcode launch --name my-agent --directory ~/myproject

# Open the supervisor dashboard
overcode supervisor

# List running agents
overcode list
```

## Features

- **Real-time TUI dashboard** - Monitor all agents at a glance
- **Cost tracking** - See estimated API costs per agent
- **Activity detection** - Know when agents need input or are working
- **Time tracking** - Green time (working) vs idle time metrics
- **Git-aware** - Auto-detects repo and branch for each agent

## Operating Modes

The supervisor offers three ways to view and interact with your agents. Toggle between them based on your workflow.

### Tree Mode (default)

Press `m` to toggle. Shows all agents in a compact tree layout with inline output previews.

**Best for:** Managing many agents (10+), especially when agents are frequently starting and stopping. The tree view gives you a quick overview of all activity at a glance.

### Preview Mode

Press `m` to toggle. Shows a list of agents on the left with a larger preview pane on the right.

**Best for:** Focused work with fewer agents. Pairs well with `i` (send input) - select an agent, review its output in the preview pane, then press `i` to send instructions.

### Tmux Pane Sync

Press `p` to toggle. When enabled, an external tmux pane automatically switches to show the currently selected agent's full terminal.

**Best for:** Reviewing lengthy output like plans or code diffs. Open the supervisor in one pane and a synced pane alongside it. As you navigate agents in the supervisor, the synced pane updates - you can scroll freely in that pane without affecting the supervisor.

**Setup:** Split your tmux window (`Ctrl-b %` or `Ctrl-b "`) before enabling sync.

## TUI Controls

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate agents |
| `m` | Toggle tree/preview mode |
| `p` | Toggle tmux pane sync |
| `i` | Send input to agent |
| `Enter` | Send Enter to agent (for approvals) |
| `x` | Kill selected agent |
| `n` | New agent |
| `q` | Quit |

## License

MIT
