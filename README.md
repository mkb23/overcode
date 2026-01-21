# overcode

A TUI supervisor for managing multiple Claude Code agents in tmux.

Monitor status, costs, and activity across all your agents from a single dashboard.

## Screenshots

**Split-screen with tmux sync** - Monitor agents in the top pane while viewing live agent output below. Press `p` to enable pane sync, then navigate with `j/k` to switch the bottom pane to the selected agent's window.

![Overcode split-screen with tmux sync](docs/images/overcode-split-screen.png)

> **iTerm2 setup**: Use `Cmd+Shift+D` to split horizontally. Run `overcode monitor` in the top pane and `tmux attach -t agents` in the bottom pane.

**Preview mode** - Press `m` to toggle List+Preview mode. Shows collapsed agent list with detailed terminal output preview for the selected agent.

![Overcode preview mode](docs/images/overcode-preview-mode.png)

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
