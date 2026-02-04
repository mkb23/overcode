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

This launches the dashboard immediately. You'll see an empty list since no agents are running yet.

### Creating Your First Agents

From inside the dashboard, press `n` to create a new agent. You'll be prompted for:
- **Name**: Give it a descriptive name (e.g., `frontend`)
- **Directory**: The project directory to work in
- **Prompt**: An initial task (optional)

Try creating three agents to experiment with:

1. Press `n`, name it `explorer`, point it at a project, prompt: "Explore the codebase and summarize the architecture"
2. Press `n`, name it `tests`, same project, prompt: "Find and run the test suite"
3. Press `n`, name it `docs`, same project, prompt: "Review the README and suggest improvements"

Now you have three agents working in parallel. Use `j/k` to navigate between them and watch their progress in the preview pane.

### Interacting with Agents

As agents work, you can guide them:

- **Send instructions**: Press `i` to open the command bar, type your message, and press Enter. For example: "Focus on the authentication module" or "Skip the database tests for now"
- **Approve prompts**: When an agent asks for permission (status turns red), press `Enter` to approve
- **Jump to attention**: Press `b` to quickly jump to the next agent that needs your input

## Installation (Optional)

If you prefer a permanent installation:

```bash
pip install overcode
```

Or with pipx for isolated installation:
```bash
pipx install overcode
```

Then run with just:
```bash
overcode monitor
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

# Use a different tmux session
overcode launch -n my-agent -d ~/myproject --session myteam
```

## Dashboard Overview

The dashboard shows all your agents with:
- **Status indicator** - Green (working), red (needs input), yellow (no orders)
- **Preview pane** - Live terminal output from the selected agent
- **Activity summary** - What the agent is currently doing
- **Costs** - Token usage and estimated API costs

### Basic Navigation

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate between agents |
| `Enter` | Send Enter to agent (approve prompts) |
| `i` | Send an instruction |
| `n` | Create a new agent |
| `x` | Kill agent (double-press) |
| `m` | Toggle tree/preview mode |
| `h` or `?` | Show all shortcuts |
| `q` | Quit |

Press `h` or `?` to see all available shortcuts.

## Sending Instructions

From the dashboard, press `i` to open the command bar, type your instruction, and press Enter:

```
> Fix the failing tests in src/utils.py
```

The instruction is sent directly to the selected agent.

### Standing Orders

Standing orders are persistent instructions that guide an agent's behavior. Press `o` to set them:

```
> Be concise. Focus on the auth module. Ask before changing database schemas.
```

Standing orders are shown to the supervisor daemon when deciding how to handle prompts.

## Using the Supervisor

The supervisor daemon adds automated oversight. It watches for agents that need input and can approve routine prompts based on standing orders.

Launch the monitor with the embedded supervisor:

```bash
overcode supervisor
```

Or start it separately:
```bash
overcode supervisor-daemon start
```

The supervisor:
- Monitors agents for permission prompts
- Consults standing orders to decide actions
- Logs interventions in the daemon panel (press `d` to toggle)

## Viewing Agent Output

### Preview Mode
Press `m` to switch to list+preview mode. The selected agent's terminal output appears in a preview pane.

### Direct Attachment
Press `Enter` while on an agent to attach directly to its tmux window. Press `Ctrl+b d` to detach back to the monitor.

### Split-Screen Setup
For the best experience, split your terminal:

1. **iTerm2**: `Cmd+Shift+D` to split horizontally
2. Run `overcode monitor` in the top pane
3. Run `tmux attach -t agents` in the bottom pane
4. Press `p` in the monitor to enable pane sync
5. Navigate with `j/k` - the bottom pane follows your selection

## Managing Multiple Agents

Launch additional agents for different tasks:

```bash
overcode launch -n frontend -d ~/myproject/frontend -p "Fix the login form validation"
overcode launch -n backend -d ~/myproject/api -p "Add rate limiting to the API"
overcode launch -n tests -d ~/myproject -p "Write integration tests for auth"
```

Use the dashboard to:
- `b` - Jump to the next agent needing attention
- `S` - Sort by status (stalled agents first) or priority
- `V` - Set priority values for custom ordering
- `z` - Put agents to sleep when you don't need them active

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
