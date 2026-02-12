"""
Bundled skill content for installation into Claude Code skill directories.

Skills are installed as directories with a SKILL.md entry point, following
the Claude Code skill format. Each skill has a description and content field.
"""

OVERCODE_SKILLS: dict[str, dict] = {
    "overcode": {
        "description": "Overcode CLI for managing Claude Code agent sessions in tmux",
        "content": """\
---
user-invocable: false
---

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

## Agent Hierarchy (#244)

Agents can spawn child agents, creating a tree:

```bash
# Launch a child agent (auto-detects parent from env)
overcode launch --name child-task --follow --prompt "Do something" --bypass-permissions

# Explicit parent
overcode launch --name child-task --parent my-agent --prompt "Do something"

# Follow an already-running child
overcode follow child-task

# Kill parent + all descendants (default)
overcode kill my-agent

# Kill only the parent, orphan children
overcode kill my-agent --no-cascade

# Show a subtree
overcode list my-agent

# Budget transfer
overcode budget transfer parent-agent child-agent 2.00
overcode budget show
```

See the `delegation` skill for detailed delegation patterns.

## Tips

- Use meaningful names to track what each agent is doing
- Use `--directory` to scope agents to specific repos/folders
- All agents are interactive - attach anytime to take over
- The supervisor TUI provides keyboard shortcuts for common actions
""",
    },
    "delegation": {
        "description": "Delegate work to child Claude agents via overcode launch",
        "content": """\
---
description: Delegate work to child Claude agents via overcode launch
---

# Delegation: Using Overcode Agents Instead of Subagents

When you need to delegate work to another Claude instance, use `overcode launch` instead of the Task tool. Overcode agents are full Claude Code sessions running in tmux — the human can watch them work in real-time, intervene, send instructions, and control them from the TUI. Built-in subagents are invisible black boxes by comparison.

## When to Use Overcode Agents

**Use `overcode launch` when:**
- The task is substantial (would take more than a few minutes)
- The human might want to observe or intervene
- You want the child to have its own git workspace and full tool access
- You're doing parallel work across multiple repos or directories
- The task involves writing code, running tests, or making commits

**Use the built-in Task tool when:**
- Quick research or file lookup (seconds, not minutes)
- Simple, self-contained queries that don't need intervention
- You need the result inline in your current context immediately

## Sequential Delegation (Blocking)

Launch a child and wait for it to finish:

```bash
overcode launch \\
  --name fix-auth-bug \\
  --directory ~/project \\
  --follow \\
  --prompt "Fix the authentication bug in src/auth.py where JWT tokens aren't being refreshed. Run tests to verify." \\
  --bypass-permissions
```

- `--follow` blocks until the child stops, streaming its output to your terminal
- `--bypass-permissions` lets the child work autonomously (use `--skip-permissions` for safer mode)
- When the child finishes, it's marked "done" and you regain control
- Check the exit: 0 = clean stop, 1 = terminated, 130 = you interrupted

**After it returns**, verify the work:
```bash
overcode show fix-auth-bug --lines 50
```

## Parallel Delegation (Non-Blocking)

Launch several children and check on them:

```bash
# Launch multiple agents in parallel
overcode launch --name refactor-api --directory ~/project --prompt "Refactor the REST API to use FastAPI" --bypass-permissions
overcode launch --name write-tests --directory ~/project --prompt "Write unit tests for the auth module" --bypass-permissions
overcode launch --name update-docs --directory ~/project --prompt "Update the API documentation" --bypass-permissions

# Monitor progress
overcode list

# Read a specific child's recent output
overcode show refactor-api --lines 100

# Block on one when you need its result
overcode follow refactor-api
```

## Writing the Prompt

**This is the most important part.** The child starts a fresh Claude Code session with zero context. Your `--prompt` must be entirely self-contained:

- **State the goal clearly** — what should be different when the child is done?
- **Include file paths** — don't say "the auth module", say `src/auth/jwt.py`
- **Specify constraints** — "don't modify the public API", "keep backwards compatibility"
- **Include verification** — "run `pytest tests/auth/` and fix any failures"
- **Give context on architecture** — the child doesn't know what you know

Bad: `"Fix the bug"`
Good: `"Fix the JWT refresh bug in src/auth/jwt.py. The refresh_token() function on line 45 doesn't check token expiry before refreshing. Add an expiry check and update the unit test in tests/test_auth.py. Run pytest tests/test_auth.py to verify."`

## Budget Control

Prevent runaway costs by giving children explicit budgets:

```bash
# Transfer from your budget to the child
overcode budget transfer my-agent fix-auth-bug 2.00

# Or set directly on the child
overcode budget set fix-auth-bug 3.00

# Check all budgets
overcode budget show
```

When a child exceeds its budget, heartbeats and supervisor nudges stop — it naturally winds down.

## Managing Children

```bash
# See your subtree
overcode list my-agent

# Kill a child (and its children, recursively)
overcode kill fix-auth-bug

# Kill only the child, not its grandchildren
overcode kill fix-auth-bug --no-cascade

# See done children
overcode list --show-done

# Clean up all done children
overcode cleanup --done
```

## Naming Convention

Use descriptive, task-oriented names. The human sees these in the TUI:

- `fix-auth-bug` not `child-1`
- `refactor-api-v2` not `task`
- `test-payment-flow` not `worker`

## Hierarchy Rules

- Parent is auto-detected from `OVERCODE_SESSION_NAME` env var — you don't need `--parent` when running inside an overcode agent
- Maximum depth is 5 levels (you → child → grandchild → ...)
- `overcode kill` cascades to all descendants by default
- The TUI shows the tree when sorted by hierarchy (press `S` to cycle to "Tree" mode)
""",
    },
}
