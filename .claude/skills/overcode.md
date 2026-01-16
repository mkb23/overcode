# Overcode CLI Skill

Overcode manages multiple Claude Code agent sessions in tmux. Use this skill to launch, monitor, and control parallel Claude agents.

## Quick Reference

```bash
# Launch a new agent
overcode launch --name <name> [--directory <path>] [--prompt "<initial prompt>"]

# List running agents
overcode list

# Show output from an agent
overcode show <name> [--lines 50]

# Send input to an agent
overcode send <name> "your message"      # Send text + Enter
overcode send <name> enter               # Press Enter (approve)
overcode send <name> escape              # Press Escape (reject)

# Attach to tmux to view/control agents
overcode attach

# Kill an agent
overcode kill <name>

# Launch supervisor TUI
overcode supervisor [--restart]

# Set standing instructions for an agent
overcode instruct <name> "Your instructions here"
```

## Launching Agents

### Basic Launch
```bash
overcode launch --name my-agent
```

### Launch with Working Directory
```bash
overcode launch --name backend --directory /path/to/backend-repo
```

### Launch with Initial Prompt
```bash
overcode launch --name feature-agent --prompt "Implement the user authentication feature"
```

## Viewing Agents

### Attach to Tmux Session
```bash
overcode attach
```

**Inside tmux:**
- `Ctrl-b 0/1/2/...` - Switch to window by number
- `Ctrl-b n` - Next window
- `Ctrl-b p` - Previous window
- `Ctrl-b d` - Detach (agents keep running)

### Direct Tmux Access
```bash
# Attach to default session
tmux attach -t agents

# List windows
tmux list-windows -t agents

# Read agent output
tmux capture-pane -t agents:<window_num> -p -S -50
```

## Supervisor TUI

The supervisor provides a dashboard for monitoring all agents.

```bash
# Launch supervisor
overcode supervisor

# With auto-restart on exit
overcode supervisor --restart
```

### Status Indicators

| Status | Meaning |
|--------|---------|
| GREEN | Running actively |
| YELLOW | Running but no standing instructions |
| ORANGE | Waiting for supervisor |
| RED | Waiting for user input |

## Session State

Sessions are tracked in `~/.overcode/sessions/<session-name>/`:

```bash
# View session state
cat ~/.overcode/sessions/agents/sessions.json | jq
```

## Unblocking Stuck Agents

When an agent is RED (waiting for input):

```bash
# See what it's stuck on
overcode show my-agent --lines 100

# Approve a permission (press Enter)
overcode send my-agent enter

# Reject a permission (press Escape)
overcode send my-agent escape

# Send a text response
overcode send my-agent "yes"
```

## File Locations

```
~/.overcode/
├── sessions/<session-name>/
│   ├── sessions.json           # Session state and metadata
│   ├── monitor_daemon.log      # Monitor daemon log
│   └── supervisor_daemon.log   # Supervisor decisions log
├── config.yaml                 # Optional configuration
├── presets.json               # Launch presets
└── presence_log.csv           # User presence tracking (macOS)
```

## Common Workflows

### Run Multiple Parallel Agents
```bash
overcode launch --name frontend --directory ./frontend --prompt "Implement the dashboard"
overcode launch --name backend --directory ./backend --prompt "Add the API endpoint"
overcode launch --name tests --directory . --prompt "Write integration tests"

# Watch them all
overcode attach
```

### Interactive Supervision
```bash
# Launch supervisor for monitoring
overcode supervisor

# Agents show in the TUI, navigate with j/k, attach with Enter
```

## Tips

- Use meaningful names to track what each agent is doing
- Use `--directory` to scope agents to specific repos/folders
- All agents are interactive - attach anytime to take over
- The supervisor TUI provides keyboard shortcuts for common actions
