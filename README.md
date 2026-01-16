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

## TUI Controls

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate agents |
| `Enter` | Attach to agent's tmux pane |
| `f` | Focus agent (full screen) |
| `k` | Kill selected agent |
| `q` | Quit |

## License

MIT
