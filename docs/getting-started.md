# Getting Started

This guide walks you through trying overcode and launching your first agents.

## Prerequisites

Before using overcode, ensure you have:

1. **Python 3.12+** - Check with `python --version`
2. **tmux** - Install via `brew install tmux` (macOS) or `apt install tmux` (Linux)
3. **Claude Code CLI** - Install from [Anthropic's documentation](https://docs.anthropic.com/en/docs/claude-code)

Verify Claude Code is working:
```bash
claude --version
```

## Quick Start

The fastest way to try overcode is with `uvx` (comes with [uv](https://docs.astral.sh/uv/)):

```bash
uvx overcode monitor
```

This launches the standalone dashboard. You'll see an empty list since no agents are running yet.

### Creating Your First Agents

From inside the dashboard, press `n` to create a new agent. You'll be prompted for:
- **Name**: Give it a descriptive name (e.g., `frontend`)
- **Directory**: The project directory to work in
- **Prompt**: An initial task (optional)

Try creating three agents:

1. Press `n`, name it `explorer`, point it at a project, prompt: "Explore the codebase and summarize the architecture"
2. Press `n`, name it `tests`, same project, prompt: "Find and run the test suite"
3. Press `n`, name it `docs`, same project, prompt: "Review the README and suggest improvements"

Now you have three agents working in parallel. Use `j/k` to navigate between them.

## Tmux Split Layout (Recommended)

For the best experience, use the tmux-native split layout:

```bash
pip install overcode
overcode tmux
```

This creates a two-pane layout:
- **Top pane**: Compact dashboard showing all agents
- **Bottom pane**: The focused agent's live terminal — native tmux, full speed

### Navigation

| Key | Where | Action |
|-----|-------|--------|
| `j/k` | Top pane | Navigate agents — bottom pane follows |
| `Tab` | Anywhere | Toggle focus between top and bottom pane |
| `Option+J/K` | Bottom pane | Navigate agents without switching focus |
| `PageUp/Down` | Bottom pane | Scroll agent's terminal history |
| `Enter` | Top pane | Approve agent permission prompts |
| `i` | Top pane | Send an instruction to the agent |
| `q` | Top pane | Detach (return to your previous tmux session) |

The bottom pane is a real tmux terminal. When it has focus, all keystrokes go directly to the agent. Use `Tab` to return to the dashboard.

### How It Works

`overcode tmux` creates:
1. An `overcode` tmux session with a split window
2. A linked session (`oc-view-agents`) that shares windows with your `agents` session
3. Keybindings scoped to the split window (Tab, Option+J/K, etc.)

The dashboard's navigation drives window switching in the linked session, so the bottom pane always shows the selected agent.

### Resizing

Press `=` to grow the dashboard pane, `-` to shrink it.

### Uninstalling

```bash
overcode tmux --uninstall
```

This removes keybindings and kills the split window and linked sessions.

## Standalone Monitor

If you prefer not to use the split layout, the standalone monitor works in any terminal:

```bash
overcode monitor
```

Press `m` to toggle list+preview mode for a side-by-side view of agent list and terminal output.

## Installation

For permanent installation:

```bash
pip install overcode
```

Or with pipx for isolated installation:
```bash
pipx install overcode
```

## Launching Agents from CLI

You can also launch agents from the command line:

```bash
overcode launch --name my-agent --directory ~/myproject
```

This creates a tmux session called `agents` (if it doesn't exist) and starts Claude Code in a new window.

### Launch Options

```bash
# With an initial prompt
overcode launch -n my-agent -d ~/myproject -p "Review the codebase structure"

# Skip permission prompts (auto-deny)
overcode launch -n my-agent -d ~/myproject --skip-permissions

# Bypass all permissions (use with caution)
overcode launch -n my-agent -d ~/myproject --bypass-permissions

# Restrict agent to specific tools
overcode launch -n my-agent -d ~/myproject --allowed-tools "Read,Glob,Grep,Edit"

# Use a different tmux session
overcode launch -n my-agent -d ~/myproject --session myteam
```

## Interacting with Agents

As agents work, you can guide them:

- **Send instructions**: Press `i` to open the command bar, type your message, and press Enter
- **Approve prompts**: When an agent asks for permission (status turns red), press `Enter` to approve
- **Jump to attention**: Press `b` to quickly jump to the next agent that needs your input

### Standing Orders

Standing orders are persistent instructions that guide an agent's behavior. Press `o` to set them:

```
> Be concise. Focus on the auth module. Ask before changing database schemas.
```

## Using the Supervisor

The supervisor daemon adds automated oversight:

```bash
overcode supervisor
```

The supervisor:
- Monitors agents for permission prompts
- Consults standing orders to decide actions
- Logs interventions in the daemon panel (press `d` to toggle)

## Cleaning Up

Kill a specific agent:
```bash
overcode kill my-agent
```

Or from the dashboard, press `x` twice on the selected agent.

Remove terminated sessions from tracking:
```bash
overcode cleanup
```

## Next Steps

- [CLI Reference](cli-reference.md) - All commands and options
- [TUI Guide](tui-guide.md) - Master the dashboard shortcuts
- [Configuration](configuration.md) - Customize behavior with config files
- [Advanced Features](advanced-features.md) - Sleep mode, handover, remote monitoring
