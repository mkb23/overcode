# Overcode CLI Skill

Overcode manages multiple Claude Code agent sessions in tmux. Use this skill to launch, monitor, and control parallel Claude agents.

## Quick Reference

```bash
# Launch a new agent
overcode launch --name <name> [--directory <path>] [--prompt "<initial prompt>"] [--skip-permissions]

# List running agents
overcode list

# Show output from an agent
overcode show <name> [--lines 50]

# Send input to an agent (unblock it!)
overcode send <name> "your message"      # Send text + Enter
overcode send <name> enter               # Just press Enter (approve)
overcode send <name> escape              # Press Escape (reject)

# Attach to tmux to view/control agents
overcode attach

# Kill an agent
overcode kill <name>

# Launch supervisor TUI + controller
overcode supervisor [--restart]

# Run background daemon (auto-supervises stuck sessions)
overcode daemon [--interval <seconds>]

# Watch logs
overcode watch daemon|supervisor
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
overcode launch --name feature-agent --prompt "Implement the user authentication feature. Start by reviewing the existing auth code."
```

### Skip Permission Prompts
```bash
overcode launch --name auto-agent --skip-permissions --prompt "Run the test suite and fix any failures"
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

## Supervisor System

The supervisor enables autonomous management of agent sessions.

### Components

1. **Monitor** - TUI dashboard showing session states (top pane)
2. **Controller** - Interactive Claude for user commands (bottom pane)
3. **Daemon** - Background loop that auto-launches supervisor when sessions need help

### Start Supervised System
```bash
# Terminal 1: Background daemon
overcode daemon --session agents --interval 10

# Terminal 2: TUI + Controller
overcode supervisor --restart
```

### Status Indicators

| Status | Meaning |
|--------|---------|
| GREEN | Running actively |
| YELLOW | Running but no autopilot instructions |
| ORANGE | Waiting for supervisor |
| RED | Waiting for user input |

## Session State

Sessions are tracked in `~/.overcode/sessions/sessions.json`:

```bash
# View all session state
cat ~/.overcode/sessions/sessions.json | jq

# View specific session
cat ~/.overcode/sessions/sessions.json | jq '.[] | select(.name=="my-agent")'
```

## Unblocking Stuck Agents

When an agent is RED (waiting for input), use these commands to unblock it:

### Check What an Agent is Waiting On
```bash
# See the agent's current output
overcode show uk-hikes

# Or with more lines
overcode show uk-hikes --lines 100
```

### Send Responses to Unblock
```bash
# If asking "Do you want to proceed?" - send "yes"
overcode send uk-hikes "yes"

# If waiting for a permission prompt - press Enter to approve
overcode send uk-hikes enter

# If you want to reject a permission - press Escape
overcode send uk-hikes escape

# Send any text response
overcode send uk-hikes "Focus on the core feature first"
```

### Example Workflow
```bash
# 1. Check status
overcode list
# Output: 🔴 uk-hikes ... Do you want to proceed?

# 2. See full context
overcode show uk-hikes --lines 30

# 3. Unblock it
overcode send uk-hikes "yes"

# 4. Verify it's running again
overcode list
# Output: 🟢 uk-hikes ... Running task...
```

## Controlling Sessions via Tmux (Advanced)

Direct tmux commands for fine-grained control:

```bash
# Send text to a session (with Enter)
tmux send-keys -t agents:<window_num> "your message here" C-m

# Send text without Enter
tmux send-keys -t agents:<window_num> "partial text"

# Approve a permission request (press Enter)
tmux send-keys -t agents:<window_num> "" C-m

# Reject a permission request (press Escape)
tmux send-keys -t agents:<window_num> Escape
```

## Common Workflows

### Run Multiple Parallel Agents
```bash
# Launch agents for different parts of a project
overcode launch --name frontend --directory ./frontend --prompt "Implement the dashboard component"
overcode launch --name backend --directory ./backend --prompt "Add the API endpoint for user stats"
overcode launch --name tests --directory . --prompt "Write integration tests for the new features"

# Watch them all
overcode attach
```

### Autonomous Mode with Daemon
```bash
# Start daemon to auto-manage sessions
overcode daemon &

# Launch agents with skip-permissions for full autonomy
overcode launch --name auto-worker --skip-permissions --prompt "Refactor the utils module"
```

### Interactive Supervision
```bash
# Launch supervisor for manual oversight
overcode supervisor

# Agents show in top pane, you interact via bottom pane
# Use bottom Claude to: set instructions, check logs, intervene
```

## File Locations

```
~/.overcode/
├── sessions/
│   └── sessions.json    # Session state and metadata
├── daemon.log           # Daemon activity log
└── supervisor.log       # Supervisor decisions log
```

## Architecture

```
┌─────────────────────────────────────┐
│   Tmux Session: "agents"            │
│   ├─ Window 0: Agent #1             │
│   ├─ Window 1: Agent #2             │
│   └─ Window N: Agent #N             │
└─────────────────────────────────────┘
         │
         ├─ State: ~/.overcode/sessions/
         └─ Logs: ~/.overcode/*.log
```

## Tips

- Use `--skip-permissions` for fully autonomous agents
- Set meaningful names to track what each agent is doing
- Use `--directory` to scope agents to specific repos/folders
- The daemon + supervisor combo enables hands-off operation
- All agents are interactive - attach anytime to take over
